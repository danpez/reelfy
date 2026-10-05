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
                    (Grader.prepare) en un hilo; luego anclas por item (_anchor, _face0) y la
                    colocacion del PiP automatico (graphics.media sin x/y, set_media_auto). Sin
                    matte, la barra avanza con face_track (0-60 %) y Grader.prepare (60-90 %)
     medios       : prepare_media: imagenes y clips del usuario (items con `media`) normalizados
                    una vez en la cache al tamano del EXPORT (el draft los lee escalados):
                    media_<clave>.png (EXIF/irot, HEIC en rejilla, color a sRGB, recorte) y
                    media_<clave>.mp4 (tramo [media_in, +duracion] a los fps del job, recorte,
                    tonemap HDR, al tamano de su hueco). Un medio ilegible se recuerda y su item
                    se omite del export (drop_failed_media)
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
| `ensure_analysis(plan, opts=None, prep=None) -> dict` | base + rostro + mattes + grade en cache, medios propios normalizados (`prepare_media`) y precalentamiento de la musica (`opts.prefetch_music`, default True). Pensado para el Studio al abrir un proyecto. |
| `prepare(plan, opts=None) -> {plan, opts, timeline, mods, job, skipped, source}` | normaliza y arma el job sin tocar video (lo usan las demas). |
| `analysis_key(plan, opts=None, prep=None) -> {"base", "full", "media"}` | claves PUBLICAS del analisis de fondo (sha1 de 16 hex, no toca video: 3-5 ms en caliente; la PRIMERA llamada del proceso carga los modulos de efectos y tarda segundos). `base` = video base + voz (fuente, cortes cuantizados, formato/reencuadre/`air`, fps, camara, `enhance_audio`); `full` = base + pista de rostro (version de camera y uniones), rangos de matte (inset, calidad, version del algoritmo) y medicion del grade (activo + tomas en tiempo final); `media` = firma de los medios propios (`media_signature`: archivo, recorte, `media_in`/`loop`, ajuste, hueco y duracion del item; None sin medios). Textos, graficos, escenas, SFX, musica y el look NO cambian `base` ni `full`. Si solo cambia `media`, el servidor llama `ensure_media` (sin relanzar el analisis). El servidor la usa en vez de `_base_files`/`_face_file`. |
| `ensure_media(plan, opts=None, prep=None) -> {items, skipped, t}` | normaliza los medios propios del plan en la cache (lo mismo que hace `ensure_analysis`), sin tocar base ni analisis: para cuando el usuario agrega o cambia un medio. |
| `PRO_OPTS`, `logo_cfg(v)` | opciones del proyecto que render_pro/render_draft/render_frame aplican: `("logo", "air", "enhance_audio")` (el servidor las anuncia en `/settings.pro_opts`). `logo_cfg` sanea el logo: None/False = sin logo; True = la marca con defaults; dict `{pos: tl\|tr\|bl\|br (o top-left...), size: 0.03..0.5 del ancho de la caja, opacity: 0..1, path?, enabled?}`; `path` default `paths.BRAND/logo.png` (si no existe: `skipped` `logo:archivo`). |
| `media_path(media, opts)`, `prepare_media(job, opts, cancel, only_images)`, `drop_failed_media(job)`, `media_signature(job)`, `fit_media(img, w, h, fit, focus, zoom)`, `media_auto_place(face, w, h, cap_top, avoid)`, `photo_caption_y(item, cap_y)` | medios propios (ver 4.2b). |
| `decode_image(src, max_w, max_h, crop=None) -> RGBA`, `image_size(src) -> (w, h)`, `image_thumb(src, out, width=320) -> (w, h)` | imagenes del usuario como las ve el render: orientacion EXIF/irot, HEIC/HEIF en rejilla de teselas (ffmpeg sin filtros; ffprobe da el tamano de UNA tesela), perfil ICC o nclx Display P3 a sRGB. `image_size` es barata (cabecera; HEIC por el grupo de teselas de ffprobe). `image_thumb` es para POST /sources (miniatura JPEG + dims de display). |
| `save_png(rgba, path)`, `output_size(fmt, src_info)`, `layout_box(W, H)`, `stage_plan(pesos)` | utilidades. |
| `music_cfg(plan, job, tl)`, `prefetch_music(...)`, `sfx_events(...)` | plomeria de audio (misma cfg para prefetch y mezcla). |
| `transition_frac(item, mods)`, `transition_dur(item, mods)` | ventana de transicion (ver 4.3). |

`opts`: `fmt` (`9:16`\|`1:1`\|`4:5`\|`16:9`\|`original`; otro = ValueError), `W`/`H` explicitos, `fps`
(30), `quality` (`final`\|`draft`), `workers` (default `min(4, cpu//3)`; 0/1 = en proceso),
`cache_dir` (default `WORK/motion_cache`), `music_cache_dir` (default el de music.py), `stems_dir`
(voz/musica/sfx por separado, debug), `enhance_audio` (True; None = True), `air` (margen
desenfocado 0..0.35 para fuentes horizontales en salida vertical; se acota), `logo` (ver
`logo_cfg`), `media_root` (carpeta o lista de carpetas de los medios propios; default
`paths.INPUT/sources`, la de POST /sources), `media_resolver` (callable `media -> ruta|None`; gana a
`media_root`), `media_bg` (True: /frame normaliza en segundo plano el clip que aun no esta en cache),
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
media{items, t, dropped} (medios propios normalizados, su tiempo y los ids omitidos por un medio
ilegible; `items` se recalcula tras omitirlos), audio{...resultado de audio.mix}, sfx_events,
t_audio, t_audio_wait, t_compose, workers, chunks, threads_per_worker, ms_per_frame_{wall,proc,
compose,io}, profile_ms_per_frame{etapa: ms}, errors{renderer: n}, t_mux, shorts, t_total`.
`warnings` que la UI debe mostrar: `transitions:<id>:sin_union` (mezcla sin union real cerca),
`matte:<a>-<b>:calidad_baja` (quality < 0.85: la persona tocaba muebles; evitar contorno o
reemplazo de fondo ahi), `matte:sin_compuerta_de_persona`.
`skipped` de medios propios: `media:<id>:no_encontrado` (el sid no esta en `media_root`; el item
se omite), `media:<id>:sin_medio` (graphics.media sin archivo), `media:<id>:ilegible` (no se pudo
decodificar: el item se OMITE del export y del draft; en /frame se dibuja un marcador para que se vea
en la edicion; el fallo se recuerda por firma del archivo y no se reintenta), `avatar:<id>:no_encontrado`
(foto de perfil del CTA Seguir que no esta: el CTA se dibuja igual, con iniciales).

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
| `profile.py` | perfil de la persona (puro): reconocer enlaces/redes, texto libre -> perfil, normalizar, entidades (3.2) |

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
  "profile": {"name": "Dra. Ana Ruiz", "title": "Odontóloga", "business": "", "links": [
    {"url": "https://instagram.com/dra.ana.demo", "platform": "instagram", "kind": "social",
     "handle": "@dra.ana.demo", "display": "@dra.ana.demo", "label": "Instagram",
     "icon": "brand-instagram", "primary": true}], "use": {"lower_third": true}},
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

Claves extra que normalize CONSERVA tal cual (las escribe el director; prueba
`test_normalize_conserva_music_grid_y_entities_extra`): `entities.name_candidate` (nombre leido,
p. ej. de un letrero, sin publicar), `entities.name_src` (`voz`|`letrero`|`usuario`),
`entities.name_confirmed` (bool: solo con true el nombre sale en rotulo/seguir),
`director.music_grid` (`{bpm, beats, downbeats, drop, key}` + la huella de la pista: la rejilla
de golpes con la que se sincronizo; redirect la reutiliza si la pista no cambio) y
`director.notes`. Las claves de `entities` del contrato se normalizan a texto.

### 3.1 Catalogo de kinds (normativo; fuente de verdad: `schema.KINDS` / `GET /fx/schema`)

El catalogo publica por pista y kind TODOS los parametros que implementa el modulo dueno, con
etiqueta en espanol, rango, paso, unidad y default (adoptado de los modulos: graphics.DEFAULTS,
cutaways.DEFAULTS/SCENE_IN/SCENE_OUT/BG_COLOR_ROLES, text.PRESETS, transitions.PARAMS,
music.CFG_SCHEMA). Tipos: `num` (con `len: 2` = punto [x, y]), `text` (`list: true` = lista),
`color` (hex o nombre: white, warm, cool, gold, rose, accent, accent2, primary, dark, light,
brand), `select`/`seg` (`opts`), `bool`, `font`, `icon`, `lines` (lineas de texto con estilo),
`rows` (lista de objetos con `fields`, p. ej. music.stops) y `media` (medio propio subido por
POST /sources: `{sid, type: image|video, name, w, h, dur}` (+ `thumb`, `crop` opcionales);
`accept` = tipos admitidos; normalize (`schema.norm_media`) exige un `sid` de `[A-Za-z0-9_-]` y
nunca acepta una ruta). `nullable` con default null = "automatico" (lo decide el renderer).
normalize() solo completa comunes y `req`; con `fill=True` completa todo. Etiquetas, ayudas y
descripciones en espanol con su ortografia (UTF-8; `schema.accent_es` corrige tambien lo que
adoptan los modulos). Cada transicion publica `mixer` (bool: mezcla A->B, sin `align`).

**text** (`preset` en title_pop, stack, cascade, serif_elegant, script_accent, behind_giant,
highlight_marker, typewriter, counter, kicker): `text` o `lines` [{text, scale, font, color,
italic}], `x`, `y`, `size`, `font`, `color`, `accent`, `accent_words` [str], `align`, `stroke`,
`shadow`, `anim_in` (pop|rise|slide_left|slide_right|type|mask|fade|scale_down|word_stagger|
letter_stagger), `anim_out` (fade|pop_out|slide_up|none), `behind`, `box`. counter: `from`, `to`,
`prefix`, `suffix`, `decimals`, `sep` (","), `anchor` (right|center|left: de donde crece),
`count_dur` (s del conteo; null = min(1.3, max(0.4, 0.55 x duracion))).
render agrega `_words` (palabras habladas del rango, tiempo final relativo al item).

**captions**: `preset` (clean_bold|karaoke|boxed|minimal_small|bold_caps|none), `y`, `x`, `size`,
`font`, `color`, `accent`, `case`, `max_words`, `hide_during_titles`, y finos (null = el estilo):
`max_lines` (1..4), `kw_accent`, `recenter`, `stroke`, `shadow`, `box`, `highlight`, y
`y_ranges` (rows `[{start, end, y}]` en tiempo ORIGINAL: altura por tramos, p. ej. al pecho en
tomas de cuerpo completo; normalize descarta filas invalidas, acota `y` a 0.1..0.9 y ordena).

**graphics** (todos: `x`, `y` (nullable: vacios + `anchor` = junto a la cara), `size`, `color`,
`anim_in`, `anim_out`, `rotation`, `in_dur`, `out_dur`):
- `doodle`: `name` (20 figuras), `stroke`, `draw`, `wiggle`, `path_to` [x,y], `style`
  (brush|sketch), `anchor` (face|face_left|face_right|face_top), `bend` (-1..1, flechas),
  `glow`, `shadow`, `draw_time`.
- `icon`: `name` (Tabler), `variant` (outline|filled), `badge` (none|circle|squircle),
  `badge_color`, `draw`, `style` (clean|sketch), `anchor`, `shadow`.
- `card`: `variant` (price|location|schedule|link|phone|rating|checklist|stat|generic|booking),
  `title`, `subtitle`, `value`, `items`, `icon`, `theme` (light|dark|brand), `accent`, `label`
  (boton), `platform` (icono de la red o pagina en link/booking; red social = insignia con su color
  de marca) y `display` (enlace corto: subtitulo si no hay). `booking` = reservas: encabezado con
  icono + `title`/`display`, chips de `items` y boton `label` ("Agendar cita").
- `lower_third`: `title`, `subtitle`, `theme` (dark|light), `accent`.
- `cta`: `variant` (follow|link_below|comment|save|share), `label`, `handle`, `subtitle`,
  `button`, `button_done`, `button_color`, `avatar` (initial|face|image). El director usa ademas
  `subtitle` y `handle` = rol en `follow` sin nombre confirmado, y `arrow_scale` 0 (sin flecha)
  en `link_below`. Perfil (3.2): `title` (follow: nombre en la primera linea; la segunda = insignia
  de `platform` + `handle`; la pastilla crece lo justo para que quepan, hasta SAFE), `platform`,
  `initials` (vacio = del nombre: 'Dra. Ana Ruiz' -> 'AR'), `media` (foto de perfil, tipo media solo
  imagen: render inyecta `_media_image`; ausente o ilegible = iniciales), `display` (link_below: el
  enlace corto en una segunda linea; la pastilla pasa a alto 0.25 x ancho y crece hasta SAFE para
  que el enlace quepa) e `icon`. `width` (0.3..0.82 de W; null = natural): ancho COMUN de la pila de
  la tarjeta final (Seguir, Link abajo, tarjeta de ubicacion y chips con el mismo ancho y centro:
  bordes alineados); nunca achica lo que no cabe. Seguir con `title` largo: pasa a su variante corta
  (`profile.name_variants`: 'Dra. Guadalupe Fernández Castañeda' -> 'Dra. Guadalupe Fernández' ->
  'Guadalupe Fernández') en vez de puntos suspensivos; el @usuario se achica hasta 0.115 del alto
  (y el boton se compacta) pero NUNCA se trunca; la insignia de la red va grande en la esquina del
  avatar (0.44 del avatar, ~44 px a 1080, con aro blanco).
- `progress`: `color`, `anim_in` (fade|none), `anim_out` (none|fade), `thickness`, `bar_y`
  (0..1, borde superior), `margin` (px). NO usa x/y/size.
- `sticker_text`: `text`, `bg`, `fg`, `font`, `border` (default true: sin el campo se dibuja).
- `socials` (redes del perfil): `links` (rows `[{platform, text}]`: `platform` de
  `profile.PLATFORMS`, `text` = @usuario o enlace corto; vacio = la etiqueta de la red), `layout`
  (column|row: una fila que no cabe en SAFE se parte en filas), `variant` (chips|watermark),
  `theme` (light|dark), `icon_style` (brand = color de cada red, Instagram con su degradado |
  mono), `title` (encabezado en versalitas, opcional), `stagger` (s entre chips), `opacity`
  (marca de agua), `backdrop` (0..1, marca de agua: pastilla oscura translucida detras del
  usuario, 0.34: se lee sobre fondos claros). `layout: grid` = rejilla de `cols` columnas (2) con
  chips del MISMO ancho: el de `width` (ancho comun de la pila, fraccion de W) repartido, o el del
  chip mas ancho; el texto se ajusta al chip. `size` = ALTO del chip / W (0.074; watermark 0.03 =
  alto del texto); (x, y) = centro del grupo. Chip = pastilla con la insignia redonda de la red +
  texto; entran escalonados con resorte.
- `media` (imagen o clip del usuario flotando, PiP; defaults `x`/`y` null = AUTOMATICO y `size`
  0.36 = ancho maximo): `media` (tipo media), `aspect` (ancho/alto 0.3..3; null = la del
  archivo), `fit` (cover|contain, con `aspect`), `focus` [x, y] (punto del archivo que queda a la
  vista; null = centro), `zoom` (1..4), `frame` (marco blanco, default true), `radius` (px ref,
  14), `shadow` (0.55), `media_in` (s: desde donde corre el clip), `loop` (default true: repite
  el tramo [media_in, fin]; false = congela el ultimo cuadro). Geometria: ancho maximo `size` x
  caja, alto maximo 1.78 x ese ancho. Sin x/y el motor lo coloca lejos de la cara, de los
  subtitulos y de otros overlays (`render.media_auto_place`, ver 4.2b) y puede achicarlo hasta
  0.6x; los defaults de `graphics.DEFAULTS['media']` (x/y/size) no pisan esta colocacion.

**cutaways** (todas: `background` (grid_warp|gradient|paper|dark_stars|rays|blur_self|color|
halftone), `bg_colors` [hex] con `roles` por fondo (indice 0 = dominante, 1 = acento, 2 = tinta
opcional), `text.text`, `text.preset`, `anim_in`/`anim_out` (cut|fade|zoom|slide_*|circle|wipe),
`in_dur`, `out_dur`):
- `title_card`; `info_card` (tarjeta propia opcional: `variant`, `title`, `subtitle`, `value`,
  `items`; si no, la tarjeta va en `graphics` embebidos); `photo` (`caption`, `tilt`, `size`
  0.3..0.8, y `media` opcional: la foto/clip del usuario en vez del cuadro congelado, con
  `fit`, `focus`, `zoom`, `media_in`, `loop`);
  `self_broll` (`src_t` tiempo orig del material, `zoom_from`, `zoom_to` 1..4, `pan` [dx, dy], y
  `media` opcional: el clip/imagen del usuario a pantalla completa con `fit`, `focus`,
  `media_in`, `loop`).

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

### 3.2 Perfil de la persona (`plan.profile`; `motion/profile.py`, puro)

Quien sale en el video y sus enlaces. Sus datos MANDAN sobre lo que el director infiere (audio,
letreros): `entities.name` = su nombre con `name_src: "perfil"` y `name_confirmed: true`; el nombre
leido en un letrero NUNCA se usa si hay perfil (aunque el perfil no traiga nombre).

```json
{"name": "Dra. Ana Ruiz", "title": "Odontóloga", "gender": "f", "business": "", "tagline": "",
 "links": [{"url": "https://paseo.club/business/dra-ana-demo", "platform": "paseoclub",
            "kind": "booking", "handle": "", "display": "paseo.club/dra-ana-demo",
            "label": "Agenda en PaseoClub", "icon": "calendar-event", "primary": false}],
 "phone": "", "whatsapp": "+52 55 1234 5678", "email": "", "address": "Calle Olmo 8",
 "avatar": {"sid": "a1b2c3", "type": "image"},
 "use": {"lower_third": true, "follow_cta": true, "booking_cta": true, "end_card": true,
         "handle_watermark": false}}
```
(Datos ficticios.) `schema.normalize` lo sanea con `profile.normalize_profile` (vacio o invalido se
quita del plan): textos de una linea recortados, enlaces re-reconocidos desde su `url` (se respetan
`label`/`display` escritos a mano), sin duplicados, UNA red `primary` (la marcada o la primera red
con @usuario de red confirmada: la del CTA Seguir; un @ `ambiguous` solo si no hay otra), `avatar`
solo por `sid` (nunca una ruta), `use` con defaults. `kind`: social | booking | web | messaging |
maps | email | phone. **Cambiar la red de un enlace** (Studio): mandar el mismo enlace con otro
`platform`; si el enlace era un @usuario `ambiguous`, o su `url` es la armada con ese usuario
('https://instagram.com/<usuario>'), se rehace en la red elegida (url, icono, etiqueta; la etiqueta
automatica de la red anterior no se arrastra). Una url PROPIA pegada (con www, otra ruta) manda sobre
el selector: al pegar otra url el Studio manda `platform` null o el nuevo.

`parse_link(texto, hint)` reconoce URL (con o sin https/www/m., sin query ni utm) o @usuario:
Instagram (instagram.com, instagr.am, ig.me; publicaciones/reels sin usuario), TikTok (/@x),
YouTube (@x, /c/, /user/, /channel/, youtu.be, watch), Facebook (facebook.com, fb.com, fb.me,
m.me; profile.php y /pages/), X/Twitter, Threads, LinkedIn (in/ y company/), Pinterest (cualquier
TLD, pin.it), Snapchat (/add/ y /@), Spotify, WhatsApp (wa.me, api.whatsapp.com/send?phone,
wa.me/message, chat.whatsapp.com, numero con 'whatsapp' cerca), Telegram (t.me), Linktree /
Beacons / bio.link (kind web), reservas conocidas (paseo.club/business/<x> -> "Agenda en
PaseoClub" + `paseo.club/<x>`; holdyfy.mixiuh.online/profesional/<x> -> "Agenda en Holdyfy";
Calendly, Fresha, Booksy, AgendaPro; el slug que identifica a la persona NUNCA se pierde: hasta 44
caracteres con el dominio, 52 con un dominio corto, y si no cabe el dominio se acorta a la marca:
'holdyfy/dra-maria-de-los-angeles'), Google Maps / goo.gl/maps / maps.app.goo.gl / Waze (maps,
"Cómo llegar"), correo o mailto:, tel: o telefono (formato '+52 55 1234 5678'); cualquier otro
dominio = web con `display` = dominio + ultima parte legible de la ruta (o solo el dominio).
Iconos Tabler: brand-instagram, brand-tiktok, brand-youtube, brand-facebook, brand-x,
brand-threads, brand-linkedin, brand-whatsapp, brand-telegram, brand-pinterest, brand-snapchat,
brand-spotify, brand-linktree, world, calendar-event, map-pin, mail, phone. Un @usuario suelto
toma la red por contexto ('su instagram es @x', 'tiktok @x', 'mi canal @x', 'su twitter es @x',
'x: @x', 'en X como @x') o Instagram con `ambiguous: true` (se une a la misma cuenta si ya aparece
con otra red). En texto libre tambien vale el usuario SIN @ tras el nombre de la red con ':' ('Instagram:
dra.demo') o con 'es' si tiene forma de usuario ('mi instagram es dra.demo'; 'su instagram es muy
bueno' no).

`parse_profile_text(texto, use_llm=True)` -> `{profile, notes, llm}`: enlaces deterministas;
nombre por reglas ('Dra./Dr./Lic./Ing./Mtra./Chef' + nombre con particulas compuestas: 'Dr. Juan
Carlos de la Garza', 'Dra. María de los Ángeles Pérez'; 'soy / me llamo / se llama / ella es';
'<Profesion> <Nombre>': 'Nutrióloga Ana Sofía Martínez', 'la maestra Rosa'), acentos de nombres y
apellidos frecuentes solo si faltan ('Lucia Fernandez' -> 'Lucía Fernández'), profesion con
ortografia y el GENERO de la persona ('es odontologa' -> 'Odontóloga'; 'Dra.' + 'odontologo' ->
'Odontóloga') que corta en separadores (' - ', '|', '·', '/') y en palabras que ya no son la
profesion ('citas por...'), negocio ('consultorio / clínica / tienda ... <Nombre>'; con el tipo en
mayuscula es parte del nombre: 'Clínica Dental Sonríe', 'Barbería El Bigote') y direccion ('Av. /
Calle / Blvd. ... <numero>[, Col. X][, Ciudad]'). Con el LLM local (qwen3.5:4b, esquema JSON, 25 s)
se refinan nombre/profesion/negocio/direccion en lenguaje natural, VALIDADOS contra el texto (nunca
inventa; una plataforma nunca es el negocio): su nombre completo gana si las reglas lo cortaron y
TODAS sus palabras estan en el texto; su profesion gana si la de las reglas quedo rota; su negocio
solo tal como se escribio (con mayuscula); su direccion solo si esta completa en el texto. Sin LLM
queda la nota 'IA local no disponible: nombre y profesión por reglas'.

`name_span(palabras, nombre)` -> (i0, i1) o None: la persona DICE su nombre si hay dos palabras del
nombre seguidas o una con presentacion antes ('soy', 'me llamo', 'la doctora'); tolera una letra de
la transcripcion solo en palabras de >= 6 letras ('sonrisa blanca' NO es la 'Dra. Blanca Flores').
`name_variants(nombre)` -> del completo al mas corto (honorifico + nombre(s) + primer apellido).
`initials(nombre)` = nombre + PRIMER apellido ('Dra. Guadalupe Fernández Castañeda' -> 'GF'; 'Dr.
José Luis Pérez' -> 'JP'). `link_cta(enlace, rubro)`: accion de la pastilla de una web con la
etiqueta automatica ('Sitio web'): 'Compra en línea' (tienda, perfumeria...), 'Ordena en línea'
(comida), 'Reserva en línea' (hospedaje), 'Inscríbete aquí' (cursos), 'Todos mis links'
(Linktree/Beacons/bio.link) o 'Visita mi página'; una etiqueta escrita por el usuario se respeta.

`entities_from(profile)` -> entidades fijadas (`name`, `role` = profesion, `business`, `handle` +
`handle_platform` de la red principal, `link_label` (con `link_cta`) + `link_display` de la pagina de
reservas o web, `phone`, `address`) y `entities.profile_keys` (las que vinieron del perfil: al cambiar o
quitar el perfil se recalculan). El rubro de la voz queda en `entities.topic_role` (iconos del
rubro y el prompt de la musica no cambian por escribir la profesion: misma pista).

### 3.3 Limpieza de tomas (`plan.cuts` src `"takes"` y `plan.takes`; `scripts/takes.py`)
Quien graba de corrido repite frases hasta que le salen, deja oraciones a medias, se corrige
("son 19, no, 16") y se traba. `takes.detect(words, dur, sensitivity, audio, existing_cuts)`
propone quitar los intentos malos y dejar la ULTIMA toma completa, con reglas deterministas y
generales del espanol hablado (solo biblioteca estandar; ~0.5 s para 19 min):
- R1 reinicio: un arranque repite (difuso: raiz, prefijo de 5 letras) el inicio de una toma de
  hasta 40 s antes que queda cubierta por la nueva. Cadenas de intentos (cada uno un corte del
  mismo `group`), retomas de cola (solo se rehace el final: se corta solo ese final; nunca una
  cola que cierra la oracion tras una palabra colgante: 'Lo mas importante es [la X.] La X es lo
  mas importante' es una reformulacion) y n-gramas repetidos. Intentos que se enciman = UN corte.
  Una toma vieja que cierra una oracion y sigue con otra (>= 3 palabras) solo es retoma con una
  entrada de >= 3 palabras y si la nueva arranca oracion: si no, lo de tras el cierre es el inicio
  de la oracion que se queda. Anafora cuyo primer item esta antes ('es el primer horno que...,
  es el primer horno en el que..., es el primer horno en el cual...'): no es retoma.
- R2/R2b fragmento colgado ('no lo,' + pausa + reinicio), R3 oracion abandonada ('...' sobre
  palabra colgante; si la oracion siguiente arranca con las palabras de su cola -'...de esta
  batidora y que... Y que ademas no hace ruido'- solo se quita la cola; una oracion de mas de 12
  palabras que no se vuelve a decir despues -ni hay voz sin transcribir pegada a la siguiente:
  Whisper se trago su repeticion- ni la sigue una groseria queda como sugerencia: lleva
  informacion), R4 autocorreccion ('X, no, Y', 'perdon', 'mejor dicho', ', digo,'), R5
  groseria/comentario (solo se corta dentro de una cadena de retomas; aislado es sugerencia), R6
  palabra funcional repetida ('la la'), R7 reformulacion tras pausa larga (nivel baja), R8 (con
  audio, nivel baja) palabra colgante + >= 0.8 s de voz que Whisper no transcribio + pausa: Whisper
  suele tragarse un intento repetido ('La [masa lleva harina de,] |pausa| La masa lleva...').
- Vetos: enfasis ('muy, muy', 'no, no, no', 'asi es, asi es'), listas y anaforas ('es moderna, es
  versatil, es elegante', 'uno, dos, tres'), conectores ('o sea', 'es decir').
- Una muletilla pura ('pues,', 'eh') entre el intento malo y una pausa se va con el corte.

Bordes: `[inicio de la primera palabra quitada, inicio de la primera que se queda)` con el aire de
los silencios (`edit.GAP_PAD_OUT/IN`). Con el audio (`plan.wav`): si el inicio de Whisper cae en
plena voz (llega tarde, hasta ~0.3 s) el borde va al hueco REAL que precede a esa voz (energia
20 dB bajo la voz durante >= 50 ms: el cierre de una consonante no cuenta); si no, a la ventana de
20 ms de menor energia a +-150 ms (dentro de una palabra que se queda solo si ahi es silencio).
Whisper se suele tragar el arranque REPETIDO de la toma buena ('...que le da un... |pausa| [El
secreto es la olla de] barro, que le da un sabor' -> solo 'barro, ...'): si tras la ULTIMA pausa
(>= 0.25 s) antes de la palabra que se queda hay >= 0.6 s de voz sin transcribir pegada a ella, el
corte termina en esa pausa (la toma buena no queda a medias); si tras esa pausa casi no hay voz,
la voz sin transcribir de antes es otro intento tragado y se va con el corte. Palabras estiradas (> 1.2 s, timing crudo de Whisper: la pausa queda ANTES de la
palabra) se acotan a su final. Garantias (pruebas `tests/test_takes.py`): ninguna palabra que se
queda tiene su punto medio dentro de un corte de tomas, las quitadas si, y un corte de tomas no
deja entre el y un silencio un tramo < `timeline.MIN_FRAG` con una palabra dentro (se acorta; si
no alcanza queda como sugerencia).

Contrato de cada corte (registros APARTE de los silencios; pueden traslaparse: `keeps_from_cuts`
funde los habilitados; restaurar una toma no restaura los silencios de dentro):
```json
{"id": "tk_3f9c0a1b2d", "start": 42.31, "end": 47.95, "enabled": true, "src": "takes",
 "kind": "retoma", "conf": 0.86, "level": "alta", "reason": "Lo repites mejor en 0:48",
 "text": "Asi que este pan es...", "kept_text": "Asi que este pan es el mejor de la casa.",
 "keep_ref": {"start": 48.03, "end": 51.2}, "group": "tg_2", "w0": 118, "w1": 123, "user": true}
```
(Datos ficticios.) `kind`: `retoma` (UI "Toma repetida"), `arranque_fallido` ("Arranque en
falso"), `autocorreccion` ("Corrección"), `tartamudeo` ("Tartamudeo"), `error` ("Error"),
`digresion` ("Fuera de tema"; reservado, las reglas no lo emiten); los silencios son "Silencio".
`id` = hash del kind y de los tiempos de palabra redondeados (estable entre corridas y con o sin
audio). `w0`/`w1` = indices (inclusive) de las palabras quitadas en la lista de palabras del plan
(frases aplanadas). `keep_ref` = la toma que se queda (la del final de la cadena), null al final
del video. `user: true` = el usuario la toco (toggle = PUT `/plan` con `enabled` cambiado y
`user: true`): las re-detecciones la conservan tal cual. `edges: true` (+ `user`) = movio sus
bordes (arrastre en el carril Cortes o Inicio/Fin del inspector): tambien se conserva, y
encender la categoria no le quita `user`. `guard: true` = el motor la dejo apagada por
seguridad (dejaria una palabra en un fragmento < MIN_FRAG, o mide menos que MIN_CUT): ningun
nivel ni `apply_sensitivity` la vuelve a encender, solo el usuario. `reason` lleva la hora de
la toma que se queda en tiempo de la FUENTE; el guion de Limpieza la reescribe en tiempo FINAL
(`FXCore.takeReason`). NUNCA `src: "ai"` (`propose_trims` no propone nada si hay cortes "ai";
con cortes "takes" corre normal).

Niveles y sensibilidad: `alta` y `media` habilitados, `baja` = sugerencia (`enabled: false`).
`suave` = solo `alta`; `normal` (default) = `alta` + `media`; `a_fondo` = todo (incluida la
reformulacion R7). `takes.apply_sensitivity(cuts, s)` re-evalua sin re-detectar (respeta `user`).
`takes.merge(old, new)` reemplaza los `src "takes"` salvo los `user`/`edges` y conserva
silencios, manuales y "ai" (idempotente); el Studio hace lo mismo con `FXCore.adoptTakeCuts`.
Solo sugerencia (`baja`) aunque parezca retoma: dos oraciones paralelas cerradas que comparten
el marco y cambian 1-2 palabras de contenido ('Agregas una taza de harina. Agregas una taza de
azucar.'; en una cadena de 3+ intentos con la misma entrada si se corta), y una oracion en '...'
que la siguiente COMPLETA (minuscula, cifra o exclamacion corta: 'El secreto es... la
mantequilla.') sin voz sin transcribir de por medio. Una groseria al final de la toma buena se
corta sola (desde su clausula), nunca con la toma.

`plan.takes = {version: 1, sensitivity, topic, summary, sections: [{start, title}], stats: {n,
enabled, removed_s (union de los habilitados), suggested, by_kind: {kind: {n (todos), s (segundos
habilitados)}}}, llm, ms}`. `takes.topic(words)`: UNA llamada al LLM (inicio de cada oracion, 16
palabras; 4096 de contexto, sin razonamiento, temperatura 0; esquema `{tema, resumen,
secciones: [{start_word, titulo}]}`), tope duro de 35 s en un hilo (el analisis nunca espera mas
de ~40 s); sin LLM -> None. `plan.topic` = el tema (lo usan `plan_meta` y el director).

En `pipeline.analyze` (despues de los silencios, ANTES de los shorts): "Detectando silencios…" ->
"Buscando tomas repetidas y errores…" -> "Entendiendo el tema del video (IA)…" -> "Eligiendo los
mejores momentos (IA)…". Los shorts (`highlights.select_highlights(sents, n, cuts)`) se eligen
sobre las palabras que se quedan (`takes.effective_words`), con la duracion MIN/MAX medida en
tiempo FINAL (sin silencios ni tomas), el prompt con tiempos finales y `num_ctx` segun el largo;
un short nunca termina dentro de una toma ni a menos de 1 s antes de una, tambien de las
sugeridas (los shorts no se recalculan al cambiar el nivel) (se extiende a la toma buena o
retrocede). El b-roll se ancla al tema si no hay glosario.

Medido en un video real de 19 min (whisper crudo, sin alineador; GT de un editor con 39 cortes
recomendados): `normal` precision 1.00 / recall 0.80 por evento, 1.6 s quitados fuera del GT,
-232 s; `a_fondo` 1.00 / 0.85. El LLM como detector fue peor (recall 0.74, 100 s): solo tema.
Con el pipeline completo (whisper `-nfa` con DTW + MMS_FA, E2E por HTTP como el Studio):
`normal` precision 0.97 / recall 0.72 (el unico corte fuera del GT es una palabra que de verdad
se repite y el GT, hecho sobre otra transcripcion, no tenia), 1 palabra que el GT conserva
quitada, -218 s sobre los silencios (861.5 s -> 724.7 s). Whisper con `-nfa` se trago 4 retomas
enteras (38 palabras) que con flash-attn si transcribe (y al reves 2): lo que no se transcribe no
se puede detectar y queda en el video. Por eso `pipeline.transcribe` solo pide el DTW (`--dtw`
+ `-nfa`) cuando hace falta: sin el alineador MMS_FA (o si no carga) o con `align=False`; con el
alineador usa flash-attn (con flash-attn + MMS la limpieza dio 0.94 / 0.77). En los clips cortos
de prueba (9 clips de 3 a 38 s) las dos opciones transcriben las mismas palabras.

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
  Con `media` (medio propio) no hay frame_request: photo recibe SOLO `_media_image` (el medio sin
  ajustar; draw_photo lo ajusta al hueco con `graphics.fit_src(img, w, h, fit, focus, zoom)`;
  contain rellena el hueco con #1A1A20) y self_broll recibe `_src_image` ya del tamano de la caja
  (cover: el clip viene horneado a la caja con el recorte del `focus`, va tal cual; contain o
  imagen: `render.fit_media`, con el resto relleno por el mismo medio desenfocado a 1/8).
- **medios propios** (render.py; el renderer de `graphics.media` es el de graphics.RENDERERS):
  - resolucion `sid -> archivo` por `opts.media_resolver` o `<media_root>/<sid>.<ext>` (default
    `paths.INPUT/sources`; nunca una ruta del plan); un sid que no esta omite el item
    (`media:<id>:no_encontrado`).
  - `prepare_media` normaliza UNA vez en la cache al tamano del EXPORT (lock por archivo; lo
    comparten export, draft, ensure_media/ensure_analysis y /frame; el draft lee los mismos
    archivos escalados). Imagen -> PNG RGBA sRGB: orientacion EXIF/irot, HEIC/HEIF en rejilla de
    teselas (ffmpeg -> PNG sin `-vf`: el filtergraph de la rejilla choca con uno simple; dims de
    la imagen decodificada, no de ffprobe), perfil ICC (Display P3, Adobe RGB, CMYK) a sRGB con
    littlecms o nclx P3 por matriz, `crop` y cabe en la caja. Clip -> mp4 del tramo
    `[media_in, media_in + duracion del item]` a los fps del job (los cuadros que tenga; tonemap
    HDR->SDR bt709, `crop`, decodificacion por hardware en Mac) al tamano de su HUECO: tarjeta del
    PiP o hueco de la foto (lo justo para cubrirlo con su `zoom`, sin ampliar) o la caja completa
    en self_broll (cover horneado con `focus`; contain cabe en la caja).
  - lectura: cuadro local `k = floor(lt*fps)`; con `loop` `k % n_eff` (repite `[media_in, fin]`),
    sin loop `min(k, n_eff-1)` (congela el ultimo); lectura secuencial por item en cada worker;
    el mismo cuadro pedido dos veces no se relee.
  - por cuadro el compositor pone `item['_media_image']` (skia.Image del medio, sin ajustar al
    hueco, solo con `crop`) y en self_broll el `_src_image` de arriba. Los renderers ajustan con
    `graphics.fit_src` o `render.fit_media(img, w, h, fit, focus, zoom)` (1:1 o ampliar =
    lineal; reducir <= 2x = bicubico solo en destinos <= 0.4 MP; si no, mipmaps).
  - /frame: un clip que aun no esta en cache se decodifica suelto (ese cuadro, misma conversion
    de color) y se normaliza en un hilo de fondo (`opts.media_bg`, default True); el servidor
    puede adelantarlo con `ensure_media` cuando cambia `analysis_key()['media']`.
  - medio ilegible: el fallo se recuerda (en memoria y `media_<clave>.failed` en la cache, por
    firma del archivo + version) y no se reintenta; `drop_failed_media` lo quita del export y del
    draft (`media:<id>:ilegible`, `stats.media.dropped`); en /frame queda con
    `_media_placeholder`: graphics.media dibuja su marcador (sin `_media_image`), photo y
    self_broll reciben un marcador gris con una foto tachada.
  - el audio del clip no se mezcla.
  - **PiP automatico** (`graphics.media` con x o y null; `set_media_auto`, tras la pista de
    rostro): `media_auto_place(face, w, h, cap_top, avoid) -> (x, y, escala)` en fracciones de la
    caja. `face` = mediana de la cara en el rango del item (None sin cara); `w`, `h` = tarjeta
    (como graphics.media_box, +6 % por marco y sombra); `cap_top` = y de los subtitulos - 0.06
    (None si estan apagados); `avoid` = textos activos (franja de 0.16 en su `y` o la del preset,
    a lo ancho de SAFE) y graficos con x/y (cuadrado de `size`). Zona prohibida = cara ampliada
    (0.03 a los lados, 0.05 arriba, 0.03 abajo); zona util = SAFE y arriba de los subtitulos.
    Candidatos por escala (1, 0.85, 0.72, 0.6): lado contrario a la cara (cara a la derecha del
    centro de SAFE -> izquierda) alineado arriba con ella; el otro lado; el pecho (bajo la cara);
    arriba de la cabeza; cada uno acotado a la zona util. Gana el primero que no toca ni la cara
    ni `avoid`; luego el que no toca la cara con menor cruce; si no, el de menor cruce con la
    cara. Sin cara: esquina superior derecha de SAFE (luego izquierda, abajo). Escribe `x`, `y`
    y `size` (achicado) en el item del job, `_size0` = el pedido (la normalizacion del clip usa
    ese, no depende de la cara) y `_auto_xy`. La vista en vivo del Studio debe usar la misma
    funcion con la cara de la toma.
  - **foto con pie y subtitulos** (`caption_layout`, `photo_caption_y(item, cap_y)`): una escena
    photo con `caption` (o `text` cadena sin normalizar) y sin `keep_captions` cuyo pie escrito
    (centro `y + 0.559 pw`, media altura `0.075 pw + 0.015`, `pw = size x ancho` de la caja en
    fracciones de alto, `y` default 0.46) se cruza con la franja de subtitulos (`cap_y` +-0.05;
    `cap_y` = captions.y o la fila de `y_ranges` del usuario en el inicio de la escena) MUEVE los
    subtitulos durante la escena: debajo del marco si cabe en SAFE, si no sobre la parte baja de
    la foto (`y + 0.4745 pw - 0.062`, ~0.59 con los defaults), si no arriba del marco; sin lugar se
    ocultan. Se agrega al job una fila `{start, end, y, _auto, _id}` (tiempo ORIGINAL de la
    escena) AL INICIO de `captions.y_ranges` (gana a las del usuario en ese tramo; text._cap_dy
    toma la primera que aplica). Si el pie no toca la franja, nada cambia.
- **perfil (graphics)**: `socials` -> `draw_socials` (`socials_extent(item, ctx) -> (w, h)` px para
  acomodar el grupo; chips: alto h = size x W, insignia 0.72 h, texto Poppins-Bold 0.36 h, entrada
  `pop` escalonada por `stagger`; watermark: icono + texto blancos con sombra y `opacity`, sin
  pastilla). `draw_platform_badge(canvas, platform, cx, cy, d, a, ctx, style, fill)`: disco con el
  color de marca (Instagram degradado, TikTok con eco cian/rojo, Snapchat amarillo con icono negro),
  `mono` = disco oscuro; reservas/web/correo/telefono usan `fill` (el acento). CTA follow con
  `title`: avatar con anillo de historia de Instagram si `platform` es instagram, foto (`_avatar_photo`:
  recorte cuadrado con `focus`, reducida UNA vez por imagen y tamano) o iniciales (1-2 letras);
  boton algo mas compacto (fuente 0.24 h, relleno 0.46 h) para que quepa el nombre. `_follow_layout`
  (lo comparten ancho y dibujo): ancho = max(size, `width`, lo necesario) hasta SAFE; nombre
  nominal 0.31 h, primera variante (`profile.name_variants`) que cabe a >= 0.215 h (si ninguna, la
  mas corta a 0.19 h); @usuario 0.22 h -> minimo 0.115 h sin truncar (si no cabe, el boton baja
  hasta 0.72x); insignia de `platform` en la esquina inferior derecha del avatar (diametro 0.44 x
  avatar, aro blanco). `cta_extent` usa `_follow_width`. link_below con `display`:
  `_link_below_layout` devuelve (label, fuente, alto, icono, ancho, display, fuente_display); alto
  `LINK_TWO_LINE_H` = 0.25 x ancho, ancho = max(contenido, `width`) hasta SAFE; el enlace corto
  baja hasta 0.13 del alto antes de recortarse; icono = `icon` o el de `platform`. socials `grid`:
  chips de ancho (`width` - huecos) / `cols`, texto hasta el chip (min 0.2 h). Watermark:
  pastilla negra con alfa `backdrop` (alto 1.24 h) detras del icono y el texto. La vista en vivo
  del Studio debe replicar estos tamanos para que el cuadro exacto coincida.
- **foto de perfil (render)**: `MEDIA_KINDS['graphics']` incluye `cta` como medio OPCIONAL
  (`OPTIONAL_MEDIA`): solo imagen (un clip se ignora), normalizada como las demas; si falta el
  archivo el CTA sigue (aviso `avatar:<id>:no_encontrado`) y si es ilegible `drop_failed_media`
  NO lo omite (queda sin `_media_image`: iniciales).
- **procesos**: `_SafeMain` (oculta `__main__.__file__/__spec__` y fija OMP/BLAS mientras se
  lanzan los workers spawn) usa un CANDADO DE MODULO (RLock) + CONTADOR: dos hilos que lanzan
  procesos a la vez (export + vista previa, scripts) se serializan en ese tramo (~0.1-1 s) y solo
  la salida mas externa restaura; ningun hijo reimporta el script principal.
- **transitions**: `WINDOWS`, `MIXERS`, `DEFAULTS`, `PARAMS` (formato del catalogo), `SFX`,
  `effective_frac(item)`, `window`, `progress`, `hit_time`.
- **text**: `RENDERERS[preset]`, `draw_captions(canvas, phrases_final, cfg, t, ctx)`,
  `is_behind(item)`; `cfg['_hide']` = rangos finales sin subtitulos; `cfg['y_ranges']` puede
  traer al inicio filas internas `_auto` (foto con pie, ver medios propios).
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
2. dibujar base en canvas; medios propios activos -> `_media_image` (+ `_src_image` en self_broll)
3. items `behind` (texto/graficos) y luego el sujeto recortado encima
4. `cutaways` activos (cubren todo; fuera de 9:16 en la caja con relleno desenfocado)
5. `graphics` (por `z`, luego orden), `text` (no behind), `captions`
6. `transitions` activas (sobre el cuadro compuesto, con `prev` congelado)
6b. grano/dither final +-2 niveles de luma sobre todo el cuadro (solo final; `opts.dither`)
7. barra de progreso y logo (`opts.logo`: esquina `pos`, ancho `size` x caja, margen 3 % de la
   caja, `opacity`; igual en export, draft y /frame)
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
  huecos del detector), `_frame_req`/`_still`/`_src_image`/`_frame_failed` (escenas), `_media`
  ({src, type, kind, crop, crop_eff, media_in, loop, fit, focus, zoom, file, nw, nh (archivo), w, h
  (lectura), n, n_eff, t0, baked, failed?}: medio propio resuelto y normalizado),
  `_media_image` (el cuadro del medio en el tiempo local), `_media_placeholder` (/frame con medio
  ilegible), `_auto_xy`/`_size0`/`_xy0` (PiP automatico), y en
  transiciones `_J`, `_F0`, `_F1`, `_P`, `_dur_final`, `_snapped`. En el job: `layout` (caja),
  `matte_ranges`, `matte_box`/`matte_box_base` (inset 16:9), `cut_frames`, `warnings`,
  `grade_cfg`, `grade_on`, `captions` (con `_hide` y filas `_auto` en `y_ranges`), `captions_on`, `words`, `phrases`, `shots`
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
- Palabras del guion (`_flatten_words`): sin las de una toma repetida o un corte manual
  habilitado (punto medio dentro); los silencios y los recortes "ai" no quitan palabras (el
  timing crudo estira palabras dichas sobre la pausa). `script.topic` = `plan.takes.topic` si
  existe (tema de todo el video; el del primer tramo del guion suele ser el saludo o el canal).
  Re-dirigir tras restaurar/quitar una toma: cada frase que sigue igual (tiempos y texto)
  conserva lo que decidio la IA (enfasis y titulo trasladados al nuevo indice de palabra).
- Sin LLM/VLM: heuristicas equivalentes (nunca falla el analisis). `llm_available` reintenta
  (2 s y luego 6 s) y solo recuerda un fallo 6 s; la cache de tomas con clave de VLM solo guarda
  descripciones reales (un VLM ocupado no deja el video en heuristicas para siempre). Al
  re-dirigir tomas ya descritas se conserva `director.vlm`. El VLM tiene tope propio (12 s por
  toma, maximo 45 s) y se corta tras 2 fallos seguidos (servidor saturado); la causa queda en
  `director.warnings` ("VLM: ...; esas tomas van con heuristicas").
- Tomas (`SHOTS_VERSION` s9): ademas de `rows` (perfil de fondo por franjas) llevan `cells`
  (ROWS x COLS = 16 x 4, todo el ancho): el color LOCAL bajo un garabato o una flecha. Un trazo
  blanco sobre fondo claro lleva `border_color` (contorno oscuro, lo dibuja graphics.doodle).
- Segmentacion del guion (Whisper sin puntos en tomas largas): una palabra que abre oracion con
  mayuscula (`STARTERS`: No, Yo, Asi, Vamos, Basicamente, Permitanme...) parte la oracion salvo
  que empiece un nombre de varias palabras ('de Al Jaramain'); sin puntuacion, una pausa corta
  sola no parte (0.62 s) y nunca tras una palabra colgante (`DANGLING`: determinantes,
  posesivos, cuantificadores, preposiciones). Un bloque de titulo tampoco termina en ellas (pasan
  al bloque siguiente con su sustantivo) y del borde de un titulo se recortan las muletillas
  (`EDGE_FILLER`/`FILLER_PH`: 'Basicamente el perfume' -> 'el perfume').
- `redirect(plan, style, intensity, seed, only_ids=None)`: re-dirige respetando `locked`,
  `src:"user"` y `director.locked`.
  - Con `only_ids` ("Regenerar con IA"): OTRA version REAL de cada item pedido con el MISMO id, el
    mismo momento y el mismo contenido: titulo con otro preset/zona (re-medido: SAFE, cara, sin
    encimar), garabato de la misma familia, icono con otro badge/estilo, tarjeta/CTA con otro
    tema o color de boton, escena con otro fondo del mismo tono, efecto con otros parametros,
    transicion de otro kind, SFX de la misma familia. La variante depende de (semilla del pedido,
    `director.rev`): la misma pareja repite el resultado; como `rev` sube en cada re-direccion,
    'Regenerar' otra vez con la misma semilla propone otra cosa. `director.seed` (la del render) NO
    cambia; no se agrega ningun item. Reglas de cada variante:
    - titulo: conserva la palabra protagonista (linea grande del stack / en mayusculas); kicker
      solo con etiqueta natural; si no hay otra colocacion, cambia algo VISIBLE en su sitio
      (tinta legible contra su fondo, fuente del kit), nunca solo la entrada. Las piezas
      `tx_bghero_*` siempre asi (la otra pieza se mide contra ella);
    - acentos LIGADOS (garabato/icono de la misma frase que tocaba la tinta vieja) se mueven con
      el titulo (subrayado bajo la tinta nueva, circulo alrededor, el resto con el mismo
      desplazamiento relativo) o se apagan si ya no caben;
    - garabatos por GEOMETRIA: un circulo sigue siendo circulo (otro color, doble trazo), un
      subrayado solo subrayado u ola; color de garabatos e iconos con contraste >= 3 contra el
      fondo real (un icono sin disco que no se lee vuelve a llevar disco);
    - transicion: otro kind con el mismo aire (TRANS_SFX); si no hay, el SFX emparejado del golpe
      cambia con ella (mismo evento). SFX: otro del mismo USO (`SFX_USE`: trazo, texto, golpe,
      aire, tonal...);
    - un item bloqueado (`locked` o en `director.locked`) NO se regenera aunque se pida (nota
      `bloqueado (no se regenera)`);
    - `captions`/`music`/`grade` en only_ids: otro preset de subtitulos, otra variacion de musica
      (`seed`) u otro look. La musica nueva invalida `director.music_grid` (nota `musica nueva: sin
      sincronia de beats hasta re-dirigir`). Sin variante posible: nota `sin variante para`.
  - Lo bloqueado ocupa SU frase y su rango: ningun otro titulo en ninguna franja durante su
    rango, sus palabras cuentan como tituladas (no se repite el texto en otra zona) y una tarjeta
    bloqueada marca su dato como mostrado. Las franjas AJENAS van marcadas `lockzone:<id>`: no
    admiten titulos, pero si un CTA (`free(..., cta=True)`: Seguir, pastilla del enlace). Si el
    titulo bloqueado entra justo despues de 'link aqui abajo', la pastilla entra con su largo corto
    y `extend_link` la sostiene en la franja baja (antes re-dirigir la quitaba). El fondo del hook
    bloqueado junto con su titulo ES el fondo del hook: no se re-emite una copia (`fx_hookbg_s<N>b`).
  - Composiciones: bloquear una pieza de `tx_bghero_s<N>` bloquea la otra y su `fx_bgswap_s<N>`;
    un texto bloqueado bloquea el fondo reemplazado que le dio la tinta. Si ese fondo ya no existe
    (el usuario lo quito), la tinta se ajusta al fondo real (nota `tinta ajustada al fondo real`).
- Ids ESTABLES por frase: `<pista>_<rol>_s<N>[b|c...]` (N = frase del guion donde empieza el
  item) y `tr_cut_j<K>` en la union con la toma K. Un cambio de estilo o un re-dirigir conserva
  el id del elemento de cada frase (seleccion y bloqueo del Studio lo siguen).
- Tipografia con presencia y jerarquia: ningun titulo (tamano base) por debajo de 1.15x el
  subtitulo de su estilo (`title_floor`: ~0.074 W en dinamico) y ninguna linea por debajo de
  `MIN_LINE` = 0.052 W. Un bloque que no cabe asi prueba otra franja, se reorganiza en dos
  bloques mas cortos que entran con la voz, o se queda como subtitulo. Kicker: etiqueta EXPLICITA
  (`kicker`: el rol del video una vez, la seccion -OJO, PROMO, RECUERDA- o DATO) de >= MIN_LINE
  (`kicker_scale`) y el bloque completo como cuerpo; sin etiqueta natural se usa otro preset.
- Contenido: un bloque se titula con un numero o un nombre propio, >= 2 palabras de contenido,
  o 1 si no es mayormente muletilla. `FILLER` son muletillas GENERALES (se recortan del borde);
  `LIGHT` son palabras sin contenido propio (auxiliares, verbos ligeros, deicticos, sustantivos
  vagos): no se recortan pero no cuentan. Nunca vocabulario de un video concreto ('Tengo 20% de
  descuento', 'Hay promocion' si se titulan). La palabra protagonista nunca es muletilla ni verbo
  ligero. En videos largos (> 35 s) la cobertura objetivo baja con la duracion, las frases
  se ordenan por contenido (nombres propios, palabras clave, numeros) y ningun tramo de mas de
  7 s queda sin titulo (`_title_gaps`).
- Encuadre: una toma a camara ABIERTA (cara < 12% del alto) tiene piso de plano medio (cara
  ~0.14-0.16, `zoom_face + 0.03`) alternando con el cerrado del jump-cut, con tope de ampliacion
  `zcap` = 1.15x la resolucion de la fuente (4K vertical 1.72; 1080p 1.3). Si la pista de rostro
  muestra que la persona camina, el zoom se limita a lo que la deja ver (`_fit_zoom`). Para una
  composicion arriba de la cabeza (palabra gigante del drama, momento sobre fondo nuevo) la
  camara de ese tramo abre lo justo (`_cap_cam`, mismo factor para todos los tramos tocados).
- Drama (blanco y negro, destello, golpe grave): solo con senal lexica (frase hecha, pregunta o
  aviso/negacion fuerte); el LLM solo no basta.
- `behind_giant` SIEMPRE con `behind: true`, tamano en [0.12, 0.46] y tinta dentro de SAFE; si
  detras no se lee (oclusion), pasa a `stack` delante sobre el cabello (nunca de media frente para
  abajo: `face_core`). Palabra gigante del drama: oclusion maxima 0.42.
- Hook (primeros 2 s): en plano abierto a camara arranca CERRADO (cara ~18% del alto, zoom <=
  `zcap`) y, con recorte limpio (`matte_ok`), el sujeto va sobre el fondo del estilo
  (`STYLES[..].hook_bg`: dinamico grid_warp, energetico rays; no gasta el presupuesto de escenas)
  hasta que abre el encuadre. `propose_trims` recorta el aire muerto antes de la primera palabra
  (> `LEAD_MAX` = 0.4 s; deja `LEAD_KEEP` = 0.12 s) si el video abre a camara, y una muletilla de
  arranque ('Ok gente,', 'Bueno,': `LEAD_INTERJ`, hasta 3 palabras con un respiro limpio despues)
  como 'muletilla al inicio'. Un b-roll mudo DESPUES de la ultima palabra es la tarjeta de cierre y
  conserva `END_KEEP` = 2.8 s (las tarjetas entran tras la transicion de escena: asi se ven >=
  `END_MIN`); esa cola no cuenta como 'silencio de' en las notas. Fondos reemplazados con
  `fade_in` 0 (el del hook) estan completos desde el cuadro 0 (la portada). El hook no titula ese preambulo: arranca en la primera idea con
  contenido y una clausula vacia ('aqui estamos haciendo') se suma a la siguiente.
- Momento protagonista sobre fondo reemplazado (`_bg_hero`, dos piezas `tx_bghero_s<N>` (gigante)
  y `tx_bghero_s<N>b` (frase)): la frase ARRIBA en 1-2 lineas de 0.075-0.09 W que se escriben con
  la voz desde el inicio del bloque (completa, o su ultima clausula si no cabe) y la palabra
  protagonista GIGANTE (>= 0.13 W; behind_giant o pila delante sobre el cabello) debajo, que
  ATERRIZA cuando se dice (golpe suave), con su hueco reservado desde el inicio. Las clausulas
  siguientes sobre el mismo fondo entran en la composicion.
- Primer plano extremo (cara >= 1/4 del alto) y fuentes horizontales: el rostro se lleva al
  cuadro 9:16 (el recorte sigue la cara: `out_face`), el jump-zoom y el empuje casi no cierran y
  los titulos van en la franja `under`: BAJO LA BARBILLA (medida con la pista de rostro de todo su
  rango: quien se inclina hacia la camara no queda bajo un titulo), anclados al fondo de SAFE,
  sobre cuello / pecho y la franja de subtitulos (se ocultan). `face_core` protege hasta la
  barbilla. El rotulo y el CTA Seguir van en la franja de los subtitulos y estos se ocultan con
  una fila `captions.y_ranges` con `hide: true` (text.draw_captions la respeta, tiempo original).
  Fuera de un primer plano, el rotulo deja >= 0.04 H de aire sobre los subtitulos.
- Rol por tema solo si la VOZ lo menciona (el lugar de la toma solo refuerza); roles con acento
  (Nutrición, Perfumería, Tienda, Restaurante...).
- Audio: `music.intro_fade` por estilo (elegante 0.6, minimal 0.5, resto 0; nunca `fade_in`);
  transiciones con `dur` > 0 (el corte seco es el kind `cut`); SFX solo del catalogo. Boton
  Seguir: `button_color` legible (el acento, o `pal.dark` si el acento es blanco: minimal).
- Estilos: `dinamico` (default, referencia), `elegante`, `minimal`, `energetico`.
- **Perfil de la persona** (`plan.profile`, 3.2; `apply_profile_entities` en direct y en redirect):
  - rotulo (`use.lower_third`): nombre + profesion ('Dra. Ana Ruiz' / 'Odontóloga'; sin nombre, el
    negocio + profesion) en vez del rol + tema. Corre ANTES que titulos, datos y escenas: temprano
    (primera toma a camara despues del hook y del momento dramatico), 2.8 s (o 2.4 / 2.0), franjas
    baja y centro libres (los titulos de ese tramo van arriba). Con negocio, el subtitulo es
    'Perfumista · Olfato' si cabe a >= 0.8 de su tamano (`lt_sub_fits`; si no, solo la profesion).
    Si la persona DICE su nombre (`profile.name_span`: dos palabras del nombre o una con
    presentacion), entra cuando lo dice (o con la toma siguiente si lo dice justo antes de un corte)
    y esas palabras no se titulan. Un fondo reemplazado nunca lo deja a medias: el bloque del fondo
    lo cubre completo (hasta 1.6 s mas en su toma) o se usa otro bloque; `_fix_lt_bg` (al final)
    extiende el fondo, recorta el rotulo (>= 1.8 s) o termina el fondo antes;
  - Seguir (`use.follow_cta`): `title` = nombre (el del NEGOCIO si la red principal es de la marca:
    '@olfato.demo' -> 'Olfato'), `platform` + `handle` de la red principal, `initials`, y `media`
    + `avatar: image` con la foto de perfil. Sin nombre ni red no hay Seguir;
  - reservas (`use.booking_cta`, pagina de reservas o, si no hay, la web): la pastilla link_below
    lleva `label` ('Agenda en PaseoClub'), `display` (enlace corto, dos lineas; flecha mas corta o
    ninguna si no cabe), `platform` e `icon` (una web con la etiqueta automatica lleva la accion de
    `link_cta` segun el rubro: 'Compra en línea'). La disparan (`_book_signals`, la frase con mas
    senales): 'link', 'enlace', 'bio', 'descripcion'; 'abajo' SOLO en frase de enlace ('el link de
    abajo', 'aqui abajo', 'abajo en la descripcion'; nunca 'los dientes de abajo' ni 'de arriba
    hacia abajo'); verbos de reservar ('agenda', 'reserva', 'aparta'); 'cita', 'horarios' solo en
    la frase del CTA o con un verbo de accion ('pide tu cita'); 'pagina', 'web', 'sitio', 'perfil'
    solo con 'mi/nuestra' o junto a otra senal. La pastilla se ve >= 1.5 s en tiempo FINAL
    (`LINK_MIN`: se alarga en su toma; si no alcanza, no se pone y la lleva la tarjeta final). La
    tarjeta de horarios dice DONDE ('En PaseoClub', 'En mi página') sin repetir el enlace;
  - tarjeta final (`use.end_card`, >= `END_MIN` = 2.2 s: entra sobre las ultimas palabras si hace
    falta): pila con ANCHO COMUN (`_stack_width`: el mayor natural del Seguir y de la pastilla, >=
    0.72, <= 0.80; todo centrado en el centro de SAFE `XS`, `width` en cada pieza) de arriba abajo =
    ubicacion (sobre el b-roll de cierre: `address` del perfil o la de la voz), Seguir, chips
    `socials` con las redes y enlaces que NO se ven ya (hasta 4; la red del Seguir y la pagina de
    la pastilla no se repiten; a camara la ubicacion va como chip de mapa; rejilla 2x2 justificada
    con 2 o 4 chips, fila si caben en una linea, si no columna) y la pastilla de reservas aunque la
    voz no la mencione. `_layout_stack` prueba tamanos de mayor a menor en el hueco (b-roll:
    toda SAFE, centrada; sobre el link sostenido: pegada encima; a camara: del pecho a los
    subtitulos) y quita primero la ubicacion y luego los chips. Si a camara NO caben los chips o
    la pastilla (primer plano con la barbilla sobre los subtitulos, o el pecho ocupado), la tarjeta
    final es una ESCENA corta (`cutaways` title_card `cw_endcard_*`, fondo `blur_self`: el video
    sigue desenfocado detras y la voz continua) con Seguir + 'Encuéntrame en' + chips + reservas
    alineados, subtitulos ocultos en ese tramo (`y_ranges` hide) y el link sostenido termina donde
    entra. Lo que no cabe queda en una nota `perfil: sin espacio para <red> <usuario>, ... en el
    cierre` (y `perfil: tarjeta final como escena ...` cuando se uso la escena);
  - marca de agua (`use.handle_watermark`, apagada por defecto): `socials` watermark con el @usuario
    de la red principal y pastilla translucida (`backdrop`), en la esquina superior de SAFE
    (izquierda o derecha) que mas tiempo queda libre, SOLO en los tramos (>= 2 s) sin un titulo ni
    una escena en esa esquina (se aparta y vuelve con un fundido); zona `edge`;
  - `apply_profile(plan, profile)`: pone o quita el perfil y re-dirige al INSTANTE sin LLM (redirect
    con `keep_rev`: misma revision de variantes, el resto de la edicion queda igual; la rejilla de
    la musica se reutiliza). Lo bloqueado se queda donde esta pero con los datos nuevos
    (`_retext_locked`: nombre, profesion, red, @usuario, foto, reservas; nota `perfil aplicado
    tambien a lo bloqueado`). Nota del director: `perfil: <nombre>, <profesion>, <red> <@usuario>,
    <reservas> (<enlace>)`;
  - 'Regenerar' de un Seguir con foto alterna foto/iniciales; de `socials`, tema e iconos.
  - Un titulo en la franja centro/baja termina arriba de cualquier pastilla o tarjeta que asome
    desde abajo en su tramo (tinta de graficos `gr_*`, margen 0.012 H).

## 6. Studio (UI)
Archivos: `spike/app/static/index.html` (pantallas, modo clasico, transporte), `fxcore.js` (logica
PURA, probada en node: linea de tiempo, geometria espejo del render, fusiones), `fx.js` (editor pro),
`profile.js` (perfil de la persona) y `fx.css`. index.html pide los assets con `?v=<sha1[:8] de
fxcore.js+fx.js+profile.js+fx.css>` (una prueba lo exige: el WebView nunca usa una copia vieja).

**Pantallas y proyectos**
- Inicio: soltar videos -> ensamblado (timeline previo) -> "Aplicar IA" (`POST /compose` con
  `director`, `engine` y, si hay uno elegido, `profile`). El perfil se elige en el inicio o en la barra
  del ensamblado (selector de perfiles guardados, "Nuevo perfil…", "Editar"); el ultimo elegido se
  recuerda en `localStorage` (`reelfy.profile.sel`: solo el id; el dato vive en `/profiles`).
- `?job=ID` abre (o reabre tras recargar) un proyecto: `/status?plan=1` -> editor; con el trabajo
  analizando, la pantalla de progreso. "Proyectos recientes" usa SOLO `GET /jobs` (tema, duracion
  final, estilo, tomas, miniatura `/thumb`): ningun `GET /plan` por proyecto.
- El video del editor es el proxy 720p (`/source/{job}?proxy=1`, mismo reloj que el original): una
  peticion de 1 byte SIN cache lee `X-Reelfy-Proxy` ANTES de cargar el video. Cada URL del `<video>`
  sirve siempre el mismo archivo: con `ready`, `?proxy=1&v=<etag>`; si no, el original por su propia
  URL (`/source/{job}`), porque `?proxy=1` cambia de contenido al quedar listo y el navegador pediria
  rangos del archivo anterior (416, video roto). Con `pending` se sondea `/status` y la cabecera (la
  cabecera manda: `proxy_state` puede quedarse atras) y al quedar listo se cambia la fuente
  conservando el instante y la reproduccion; tope de 15 min. Cualquier otro estado final o la
  ausencia de la tarea deja el original y corta el sondeo; si el proxy no se puede reproducir, se
  vuelve al original en el mismo instante.

**Edicion y guardado**
- Autoguardado `PUT /plan` a 1.2 s del ultimo cambio con `base_rev` (la revision sobre la que edita
  el Studio). 409 = otra ventana o re-dirigir gano: aviso fijo con "Sobrescribir" / "Recargar"; nada
  se pisa en silencio. "Nuevo video" espera el guardado; al cerrar (`pagehide`) va con `keepalive` y
  al ocultar la pestana se guarda. No se reenvia un plan identico al que el servidor ya tiene (el
  que devolvio `/direct` o `PUT /profile`): su huella distingue `1.0` de `1` y subiria la revision.
  El plan lleva `profile: null` cuando el guardado tenia perfil y el local no (deshacer "aplicar
  perfil"); con `profile_applied` adopta el `plan` que devuelve el servidor, con `music_synced` la
  version sincronizada.
- Deshacer/rehacer: una instantanea por gesto (arrastre, slider, texto). Editar un item de la IA lo
  bloquea (`locked`); bloquear una pieza de una composicion (`tx_bghero_s<N>`, `tx_bghero_s<N>b`,
  `fx_bgswap_s<N>`) bloquea las tres (etiqueta "Composición" con "Seleccionar").
- Capa de bloqueo mientras la IA dirige: ninguna edicion ni deshacer (Espacio reproduce), Exportar y
  Nuevo video deshabilitados. "Re-dirigir todo" va en segundo plano (`/direct {full, background}`,
  progreso por paso, Cancelar = `/cancel?only=direct`); estilo, intensidad y regenerar son rapidos.
  Un instante del plan garantiza que gane lo del usuario si algo cambiara durante la direccion.
- Plan que llega del servidor (`/direct`, `PUT /profile`, la version sincronizada con la musica, el
  que se abre): `fxcore.dedupeFx` quita SOLO las copias de la IA de lo bloqueado o creado por el
  usuario (mismo contenido y solape > 50 %; en efectos, transiciones y SFX cuentan tipo Y
  parametros), y lo hace ANTES de tomar nota del plan del servidor: no cuenta como edicion, no se
  reenvia y no rompe la sincronizacion automatica. Lo que la IA propone entre si (incluidos efectos
  re-emitidos con sufijo, `fx_hookbg_s0b`) no se toca: el plan local queda identico a la propuesta
  guardada (si el director emite copias, es un defecto suyo). Para que esas re-emisiones no se
  acumulen, no viajan en la COPIA del plan que se manda a `/direct` (`fxcore.dropReemitted`: mismo
  prefijo `_s<N>`, mismo tipo, solape > 90 %, ni bloqueado ni del usuario). "Regenerar con IA"
  (`only_ids`, semilla nueva) adopta SOLO la version nueva de lo pedido y de sus piezas ligadas
  (`fxcore.regenCompanions`: el SFX de la transicion, los acentos del titulo, la composicion); el
  resto del plan del director se ignora. Si no hubo variante lo dice ("La IA no propuso cambios").
- Musica IA de la propuesta: si `/direct` responde `music.pending`, el panel Director muestra el
  avance de la tarea `music` (`/status`, sondeo acotado: solo mientras genera o sincroniza). Al quedar
  `synced` con `music.plan_rev` mayor que la revision del Studio, y si el plan local es el que tiene
  el servidor (`FX.isClean`: nada editado ni pendiente), se adopta la version sincronizada sola
  (sin paso de deshacer). Si ya la edito (`music_resync: available` o `superseded`) ofrece
  "Sincronizar con la música" (re-dirigir con la pista en cache, < 1 s), salvo que el plan ya traiga
  la rejilla de la pista vigente (`director.music_grid` con la misma firma de musica que al
  recibirlo: p. ej. tras sincronizar, que no cambia el estado de la tarea `music`). Si la adopcion
  se salta porque hay cambios sin guardar, el sondeo sigue ~15 s para ver el `superseded` que deja el
  autoguardado.

**Opciones del proyecto** (no viven en el plan; viajan en `/frame`, `/preview`, `/render`):
`GET /settings` -> `pro_opts` (logo, audio de estudio: controles activos en la edicion IA),
`pro_opts_optin` (aire: se manda `air_user: true` SOLO si el usuario movio el control; la vista en
vivo dibuja el margen solo entonces, y avisa que solo cambia un video horizontal), `pro_langs`
(idiomas de la edicion IA) y `features`. Al reabrir, `/status.engine` restaura el aire elegido, el
audio de estudio y el logo (`logo` bool; si llega un dict `{pos, size}` tambien su lugar y tamano).
Controles que el export no aplica se muestran deshabilitados con una nota.

**Idiomas** (`pro_langs`: es, en, pt, fr, de, it): elegir un idioma traduce los subtitulos
(`/translate {plan, lang}` -> `plan.phrases_<lang>`; `fxcore.planForLang` los pone como `phrases` en
lo que viaja) y queda en `plan.studio.lang`: al recargar o reabrir se usa si sus `phrases_<lang>`
existen (no se deshace con ⌘Z, como el formato). "Traducir titulos y graficos" manda
`{texts, spans, lang}` y aplica `texts_<lang>` en un paso de deshacer. El estado va POR ITEM en
`plan.studio.texts` = `{lang, src: {id: campos originales}, out: {id: campos traducidos},
untranslated}` (campos = `text` y `lines` completos, titulo/subtitulo/etiqueta de un grafico, texto
de una escena; `fxcore.translationRequest` / `applyTranslation` / `restoreOriginalTexts`): cada item
se traduce desde su original completo; lo editado a mano despues de traducir (distinto de `out`) se
respeta y la nota lo dice ("N editados a mano (se respetan)" + "Traducir los editados de nuevo");
los titulos nuevos se ofrecen aparte; "Volver a los textos originales" recupera cada item entero
(todas sus lineas aunque se hayan editado). Si los titulos van en un idioma y los subtitulos en
otro, un aviso junto a Exportar lo dice (clic = nota del idioma). Un 503 muestra el motivo (IA local
caida) y no guarda nada. El formato anterior (`src` por ruta) se sigue leyendo.

**Vista en vivo** (canvas aproximado, `FX.drawLive`) con la misma geometria que el render donde
importa: subtitulos con los grupos REALES (`POST /captions`: palabras, lineas, acentos, `rect`; se
piden de nuevo solo si cambian frases, estilo, cortes, tomas, formato o idioma; mientras llegan,
agrupacion aproximada) y la geometria de `text._layout_group` (`fxcore.captionLayout`: tamano
`size * scale` del preset, espacio entre palabras `space`, separacion `gap`, ancho util 0.96 de SAFE,
envoltura `fxcore.balancedSplit` = `text._balanced_split`, reduccion de la linea que no cabe, palabra
activa con `active_scale` y color, contorno y sombra del preset; tablas `CAPTION_PRESETS` copiadas
del motor con prueba de paridad). Con el `rect` de /captions el tamano se calibra al ancho del
export y el bloque queda en su centro y alto exactos (sin calibrar, con las medidas del motor, el
ancho coincide al 0.0 %; en Chrome con la fuente real, < 1 %). Ocultos igual que el export (`fxcore.captionsHiddenAt` = `render.build_job`, filas
`y_ranges` con `hide`, grupo tapado que no reaparece) y a la altura del export
(`fxcore.captionYAt`: filas automaticas de la foto con pie = `render.photo_caption_y`, luego las del
usuario). Titulos con aparicion por palabra alineada a la voz (o `word_sync: false`: entran
enteros), espacio y separacion de su preset (`fxcore.TEXT_PRESETS` = `text.PRESETS`), ancho util por
x/alineacion/`width`/deriva con envoltura balanceada de una linea que no cabe y reduccion de lo que
aun no cabe, acentos de serif_elegant en la italica real (`fxcore.ITALIC` = `text._ITALIC`; si no
hay variante, oblicua) y en el color del texto salvo `accent`, kicker con etiqueta, garabatos con `border_color`, CTA follow y link_below con el ancho de
la pila (`width`), nombre corto en vez de cortarlo, @usuario completo, insignia de la red en el
avatar, chips de redes (`socials`: columna, fila o rejilla con `cols`/`width`; marca de agua con
`backdrop`), rotulo claro u oscuro. Medios propios: foto instantanea (`photo`), clip o imagen a
pantalla completa (`self_broll`) y PiP (`graphics.media`) con `fit`/`focus`/`zoom`/`radius`/marco;
los clips son un `<video>` silenciado por medio sincronizado al tiempo local (`media_in`,
repeticion o congelado). Un PiP con x/y vacios se coloca con `fxcore.pipPlace` = contrato de
`render.media_auto_place` (cara mediana del rango con `shots[].face_track`, tarjeta de
`pipCard`, franja de subtitulos, overlays activos). Un medio que el navegador no decodifica (HEIC)
se ve como marcador "Vista exacta al pausar". Al pausar, el cuadro exacto del servidor (`/frame`,
`drop_stale`) reemplaza la aproximacion.

**Medios propios**: "Imagen o video propio…" en Graficos (PiP) y "Foto o clip propio…" en Escenas
(foto), soltar un archivo sobre esas pistas (`fxcore.mediaKindFor`: imagen o clip segun el tipo) o
el control del inspector ("Cambiar"). Suben por `POST /sources`; el item guarda
`{sid, type, name, w, h, dur, thumb}` y un clip dura lo que dura (1-6 s).

**Perfil de la persona** (`profile.js`, contrato 3.2): panel con nombre, profesion, negocio,
telefono, WhatsApp, direccion, enlaces como chips (pegar URL o @usuario -> `POST /profile/parse`
sin IA reconoce la red; icono de `/profile/platforms`; usuario/etiqueta y texto mostrado editables;
estrella = red principal del Seguir; un @ ambiguo avisa y se corrige con el selector de red, que
manda el mismo enlace con otro `platform`), foto (imagen por `/sources`) e interruptores de `use`.
"Pegar descripcion" manda el texto a `POST /profile/parse` (IA local) y muestra "Entendi esto"
(campos, enlaces y notas) para confirmar con "Usar estos datos": si el nombre entendido es de OTRA
persona (`PF.otherPerson`: ninguno de los dos nombres, sin titulo ni acentos, contiene al otro) se
reemplaza TODO el borrador (enlaces, telefono, WhatsApp, direccion, foto e id del perfil guardado)
con la opcion "Era la misma: completar en su lugar"; si es la misma, se completa (un enlace de la
misma red reemplaza al anterior). En ambos casos la estrella pasa a la red social del texto. Perfiles guardados: selector,
"Guardar perfil" / "Guardar cambios" (`POST /profiles`, con `id` al editar uno guardado: lo ACTUALIZA;
un doble clic guarda una sola vez; muestra el normalizado). Sin ligar y con el nombre de uno ya
guardado (el mismo nombre sin titulo ni acentos, o dos palabras en comun: un nombre de pila solo no
cuenta) el dialogo dice cual es (su resumen; con dos guardados, una opcion por cada uno) y pregunta
"Guardar otro aparte" (enfocado: Enter nunca pisa nada) o "Completar ese perfil" (le agrega lo
escrito y CONSERVA sus enlaces, telefonos, direccion y foto). Eliminar (`DELETE`, con la confirmacion
propia de `dialog.js`: en la app de escritorio Tauri/wry `window.confirm` no existe y devolvia false).
"Cambios sin guardar" = lo editado desde que se abrio el panel (o se eligio / guardo un perfil), no la
diferencia con la version guardada; con ellos el boton principal dice "Guardar y usar en el próximo
video" / "Guardar y aplicar al video" (sin editar nunca guarda). En el Studio los interruptores de
"Dónde aparece" son de ese video: no cuentan como cambios del perfil guardado ni se escriben en el
(salvo que se cambien en el panel y se pulse "Guardar cambios"). Si el panel tiene OTRA version que
la guardada (la del video, o se guardo en otra ventana) lo dice arriba ("Usar la versión guardada" /
"Mantener la de este video") y nunca la pisa sin preguntar (`base_updated`: 409 -> "Usar la versión
guardada" / "Reemplazar con la de aquí"). Cerrar (Escape, X) o elegir otro perfil con cambios pregunta
(enfocado "Seguir editando"); con el panel abierto los atajos del editor no corren detras. Guardar o
eliminar (o volver a la ventana: `focus` -> `PF.load(true)`) pone al dia las listas de Inicio, Montar
clips y Estilo sin recargar (`PF.syncAll`); dos guardados con el mismo nombre se distinguen ("Dra. Ana
Demo · Abogada"). Telefono, WhatsApp y correo: el CAMPO manda sobre su enlace (`from_field`): cambiarlo
rehace el wa.me / tel: / mailto: en su lugar (al instante con `POST /profile/normalize`, y la tarjeta
final con el numero nuevo al aplicar), vaciarlo lo quita, quitar el enlace vacia el campo y un numero
agregado aparte se queda. Cambiar la red o el @usuario de un enlace llama `POST /profile/normalize`:
la url se rehace al instante y la foto/logo de la cuenta anterior se descartan (llegan los de la
nueva); "Volver a buscar la foto/el logo en su página" los vuelve a bajar aunque ya hubiera uno
(`POST /profile/media/refresh` forzado, solo ese enlace); si la pagina responde SIN imagen (un muro de
inicio de sesion) se queda la que habia. En el Studio, si el video usa otra version del perfil
guardado, "Usar la versión guardada" la aplica. En el editor, grupo "Perfil" (pestana Proyecto): "Aplicar al video" =
`PUT /profile/{job}` con `base_rev` y el plan actual -> el Studio adopta el plan (rotulo, Seguir,
reservas y cierre al instante, ~0.1 s) con UN paso de deshacer; tambien "Usar un perfil guardado" y
"Quitar del video". Muestra las notas del director "perfil: sin espacio para…" y "tarjeta final como
escena".

**Vista previa real y export**: "Vista previa real" (`/preview` pro, 540p con audio). Si la musica
IA aun se genera (`preview_music_pending`), el Studio reproduce la provisional con aviso y sigue
sondeando: cuando cambia `preview_file` (`music_refresh: done`) cambia de archivo en el mismo
instante y estado de reproduccion; los estados finales (`canceled`, `timeout`, `error`,
`superseded`, `skipped`) muestran `preview_message`. El export muestra sus etapas y, al terminar,
los avisos del render en lenguaje humano (`fxcore.humanSkip`: `sfx:<id>:nombre_desconocido`,
`sfx:<id>:dato_invalido`, `media:<id>:ilegible|no_encontrado|sin_medio`,
`transitions:<id>:sin_union`, `matte:<a>-<b>:calidad_baja`, `music:error`, renderer faltante...) con
"Ver" para ir al elemento. Antes de exportar, un SFX con un nombre fuera del catalogo (`/fx/sfx`) ya
se marca en rojo en la pista y en el inspector ("no existe en el catálogo: no sonará"). Cancelar la
vista previa usa `?only=preview`.

**Pistas y musica**: Texto, Graficos, Escenas, Efectos, Transiciones, SFX y Musica en el timeline;
inspector guiado por `/fx/schema`. La pista Musica dibuja los pulsos y tiempos fuertes (`/music`
`beats`/`downbeats` en tiempo final -> original; sin musica generada, `director.music_grid`), los
silencios, subidas y el golpe principal (arrastrables). Panel de musica: origen, animo, BPM (0 =
auto), descripcion para la IA, "Entrada suave" (`intro_fade`), "Cola final" (`fade_out`) y volumen;
la musica se marca desactualizada solo si cambia el archivo (`fxcore.musicSig`).

**Pruebas**: `tests/js/fxcore.test.js` (node), `tests/test_studio_ui.py` (paridad con el motor:
linea de tiempo, aparicion de palabras, regla de ocultar subtitulos, PiP automatico, foto con pie,
transiciones de mezcla, presets de subtitulos y titulos, italicas, palabras funcionales, envoltura
balanceada, ancho de los subtitulos en vivo contra el `rect` del export con las medidas reales de
la fuente; ids, iconos, acentos, version de assets) y `tests/studio_smoke.mjs` (E2E en Chrome real
con Playwright; ver su cabecera: exige al menos un proyecto reciente, la sincronia AUTOMATICA con la
musica IA sin ediciones y con el hook bloqueado (sin boton ni PUT), la geometria de los subtitulos
contra el export, y falla con cualquier respuesta HTTP >= 400 inesperada).

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
| POST `/compose` y `/analyze` | + `profile` (perfil 3.2; en `/analyze` como campo de formulario JSON) o `profile_id` (uno guardado): se guarda en el trabajo y el director lo integra (400 si es invalido, 404 si el id no existe) + `director: {style, intensity, seed, music, enabled?}` (opcional; default dinamico/0.75/7; en `/analyze` va como campo de formulario JSON) + `engine: {air, air_user, enhance_audio, logo}` opcional (las opciones del proyecto que el Studio ya conoce: el primer analisis de fondo prepara el video base con ellas; en `/analyze` tambien como campo JSON; al llegar a review se guardan las EFECTIVAS para esa fuente). `music`: fuente (`ai`/`procedural`/`library`/`none`) o dict parcial de `fx.music`. `enabled:false` = sin director (plan v1). | `{job_id}`. Progreso monotono con `eta`: `stage` compose (2-32 %, avanza por clip normalizado) -> analyze (34-70 %; 2-70 % sin compose) -> director ("Dirigiendo la edicion (tomas / guion / coreografia / componiendo la musica)", 70-99 %) -> review. Si el director falla con IA cae a heuristicas y luego al plan v1 migrado; NUNCA tumba el analisis (aviso en `warnings`, tambien si el VLM cayo a heuristicas). La `music` elegida entra ANTES de sincronizar: si no es la pista IA por defecto (otra fuente, o prompt/mood/bpm/keyscale/seed/track/ending propios) el director corre con `music_sync=False` (no genera ACE ni alinea golpes a una pista que no va a sonar). compose pone en `plan.shots` los limites REALES de cada clip (`src` = nombre original). |
| POST `/direct/{job}` | `{plan, style?, intensity?, seed?, only_ids?, full?, use_llm?, music?, fmt?, background?, profile?}` (`profile`: el perfil a usar; null = quitarlo; sin la clave se conserva el del plan) | `{plan, plan_rev, warnings, ms, music: {pending}}`. `only_ids`: `redirect` solo esos items (rapido, sin LLM). `full:true` (o plan v1): `direct` completo con LLM (`use_llm:false` = heuristicas). Si no: `redirect` del plan entero. Sin `full` (ruta critica del Studio: cambio de estilo, regenerar) NUNCA espera a ACE: primero `redirect(music_sync=False)`; si la pista IA de la propuesta ya esta en cache repite con sincronia (< 1 s) y la respuesta ya va al beat; si hay que generarla responde sin sincronia (`music.pending: true`, aviso en `warnings` y nota del director) y la genera la tarea lateral `music` (ver abajo), que al terminar guarda sola la propuesta sincronizada. "Re-dirigir todo" (`full`) cancela esa tarea y sincroniza con la pista del director. Sin `music` se conserva la musica que el usuario eligio en el plan (fuente no IA, apagada o `locked`). `background:true` (con `full`): tarea lateral `direct` -> `{ok, state, background}`; progreso en `/status` (`direct_state/pct/stage/message`, `direct.plan_rev` al terminar; el plan llega con `?rev=N`); cancelable con `/cancel/{job}?only=direct` (el director termina el paso en curso y su resultado se DESCARTA). 409 si el job esta procesando o ya dirige. Relanza el analisis de fondo. |
| GET `/plan/{job}` | – | `{plan, plan_rev}` |
| POST `/profile/parse` | `{text, use_llm? (true)}` (<= 4000 caracteres) | `{profile, notes, llm, ms}`: texto libre -> perfil (3.2) para que el Studio lo muestre y confirme (NO se guarda). Enlaces sin IA; nombre/profesion/negocio por reglas + LLM local si responde (`llm: false` y nota si no). 400 sin texto. |
| GET `/profile/platforms` | – | `{platforms: [{id, label, icon, kind, color, cta?, svg}], kinds, use}`: catalogo para el selector de red de cada enlace (icono SVG Tabler de marca). |
| GET `/profiles` | – | `{profiles: [...]}`: perfiles reutilizables (`DATA/assets/brand/profiles.json`, mas recientes primero, maximo 100). |
| POST `/profiles` | `{profile, id?, base_updated?}` | `{ok, id, profile, profiles, replaced}`: con `id` (o `profile.id`) de uno guardado lo ACTUALIZA (`replaced: true`); sin id crea uno nuevo con id legible del nombre ('dra-ana-ruiz') y, si ya existe, con sufijo ('dra-ana-ruiz-2'): nunca pisa en silencio (homonimos o un guardado sin querer). `updated` va al milisegundo y siempre crece; con `base_updated` (el `updated` de la version que abrio el Studio) y un guardado que cambio despues -> **409** `{detail, id, profile, profiles}` sin pisarlo (el Studio pregunta). 400 si esta vacio o `base_updated` no es un numero. La foto (`avatar.sid`) de un perfil guardado no la borra la limpieza del disco. |
| DELETE `/profiles/{id}` | – | `{ok, profiles}`; 404 si no existe. |
| POST `/profile/normalize` | `{profile}` | `{profile}` (con `media_status`): el perfil saneado COMO SE GUARDARIA, sin guardarlo. El Studio lo pide al cambiar la red o el @usuario de un enlace: la url armada con el usuario anterior se rehace con el nuevo (una url propia con otra ruta no se toca), el texto que mostraba el anterior se va y la foto/logo AUTOMATICOS de la otra cuenta tambien (lo subido por el usuario se queda); lo ya descargado del enlace nuevo entra y lo que falta baja en segundo plano. 400 sin perfil o vacio. |
| POST `/profile/media/refresh` | `{profile}` \| `{profile_id}` \| `{job_id}` | `{ok, profile, urls, enabled}`: vuelve a bajar (forzado, en segundo plano) la foto/logo automaticos de los enlaces del perfil (el Studio manda solo el enlace de "Volver a buscar"). |
| GET `/profile/{job}` | – | `{profile, plan_rev}` del plan guardado. |
| PUT `/profile/{job}` | `{profile \| null, base_rev?, plan?, profile_id?, fmt?}` | `{ok, plan, plan_rev, profile, ms}`: pone (o quita con null) el perfil y re-dirige AL INSTANTE (`director.apply_profile`: sin LLM ni re-analisis, misma revision; rotulo, Seguir, reservas y cierre cambian; lo bloqueado se queda con los datos nuevos). Con `base_rev` es concurrencia optimista (**409** `{ok:false, plan_rev}` si cambio). 409 si el trabajo procesa o ya dirige; 400 sin perfil o invalido; 404 con un `profile_id` que no existe. Para cambiar la red de un enlace basta mandar el enlace con otro `platform` (3.2). |
| PUT `/plan/{job}` | `{plan, base_rev?}` | `{ok, plan_rev}`: autoguardado del Studio (no relanza el analisis de fondo). Con `base_rev` (revision sobre la que edito) es concurrencia optimista: si `plan_rev` ya cambio (re-dirigir u otra ventana) -> **409** `{ok:false, plan_rev}` y NO se pisa. Excepcion: si la unica revision posterior es la sincronizacion automatica con la musica IA (tarea `music`, escrita sobre `base_rev`), no hay 409: con la propuesta tal cual se queda la sincronizada (`{ok, plan_rev, music_synced: true}`); con una edicion encima gana el usuario y `music` pasa a `superseded` con `music_resync: "available"`. Si `plan.profile` difiere del guardado (normalizado), el perfil se RE-APLICA (`director.apply_profile`, ~30 ms: rotulo, Seguir, reservas y cierre con los datos nuevos) y la respuesta trae `{ok, plan_rev, plan, profile_applied: true}`: el Studio adopta ese `plan`. Sin la clave `profile` se conserva la guardada (un cliente que no la conoce no la borra); `profile: null` la quita. Lo recomendado para editar el perfil sigue siendo PUT `/profile/{job}`. |
| GET `/fx/schema` | – | `schema.schema()` (catalogo de pistas/kinds/parametros para el inspector) |
| GET `/fx/fonts` | – | `{fonts:[{name, family, role, file, weight, italic, url}], default, roles, license}` |
| GET `/fx/font/{name}` | – | archivo TTF (nombre logico o de archivo; para `@font-face`) |
| GET `/fx/icons?q=&limit=&variant=&names=` | – | `{icons:[{name, svg}]}` (Tabler MIT, SVG 24x24 `currentColor`; sin `q`: populares; `names` = lista exacta separada por comas, p. ej. los iconos de marca `brand-instagram,brand-tiktok,calendar-event`) |
| GET `/fx/sfx` | – | catalogo de SFX `{name: {dur, hit, tags, category, desc, variants, ...}}` |
| GET `/fx/sfx/{name}` | `?variant=` (0-based) | audio del SFX (flac) |
| POST `/frame/{job}` | `{plan, t_final, fmt, quality: "draft"\|"final", jpeg_quality?, drop_stale?}` + opciones del proyecto | `image/jpeg` del cuadro exacto (`render.render_frame`, uno a la vez); cabecera `X-Frame-Ms`. 400 si el plan no es v2. Con `drop_stale:true` (arrastre) un pedido que espera turno y ya fue superado por otro del mismo job responde 204. Si las opciones del proyecto EFECTIVAS cambian el video base (aire que aplica a la fuente, audio de estudio) relanza el analisis de fondo con ellas. Si el plan es la propuesta que el servidor ya sincronizo con la musica IA (mismo contenido que la propuesta y nadie edito despues), usa la sincronizada (igual en `/preview` y `/render`). |
| POST `/preview/{job}` | `{plan, pro: true, fmt}` + opciones del proyecto | `{ok, state}`; tarea lateral `preview` (`render.render_draft` 540p con audio). No mueve la fase del job. Progreso en `/status`: `preview_state` (queued/running/done/error/canceled; `queued` ya esta escrito cuando responde), `preview_pct`, `preview_stage`, `preview_message`, `preview_done`, `preview_error`, `preview_t`, `preview_file` (servir con `/video/{file}`; `null` mientras no termina). Musica IA aun generandose (`stats.music_pending`; el draft no la precalienta: `opts.prefetch_music=False`): la vista previa queda `done` con la procedural, `preview_music_pending: true` y `preview.music_refresh: "waiting"`; la tarea lateral `music` genera la pista (la unica generacion del trabajo), el servidor espera a que quede en cache (`REELFY_PREVIEW_MUSIC_WAIT`, 300 s), rehace el draft y publica el archivo NUEVO en la misma tarea (`preview_file` cambia, `music_pending: false`, `music_refresh: "done"`; avance en `preview.refresh_pct`). La provisional NO se borra: queda en `preview_prev_file` (el Studio puede seguir reproduciendola; pide rangos al hacer seek) hasta la siguiente vista previa. `music_refresh` = waiting/rendering/done/canceled/timeout/error/superseded (la edicion cambio y esa pista ya no se genera)/skipped (hay un export en curso: ya lleva la musica IA); cada estado terminal deja `preview_message` veraz ("Vista previa con la musica provisional (…)"). El Studio sigue sondeando mientras `preview_music_pending` y cambia de archivo. Cancelar (`?only=preview`) u otra vista previa detienen el refresco sin tocar la vista previa ya lista. Sin `pro`: preview legacy. |
| POST `/render/{job}` | `{plan, fmt, shorts (default true), legacy?}` + opciones del proyecto | `{ok, pro, plan_rev}`; si `plan.version==2` usa `render.render_pro` con shorts (progreso por etapa en `stage` + `pct` global + ETA; cancelable); si no, pipeline legacy. 409 si ya procesa. `clips[0].stats` trae los tiempos del render, `skipped`, `music` (source/engine/bpm/cached, sin rutas), `engine` ({air, logo, enhance_audio} aplicados) y `load`. Un export cancelado (o interrumpido por un cierre) vuelve a `phase/state: review` con `message` "Export cancelado…" (no es error). Del trabajo se conservan los 2 exports mas recientes. |
| POST `/music/{job}` | `{plan, seed?, fmt?}` | `{file, url, bpm, drop, source, engine, duration, sections, beats, downbeats, cached, warnings, ...}`: (re)genera la musica del plan con la MISMA cfg que el render (`render.music_cfg`: tras un export es acierto de cache) y deja un wav escuchable. `beats`/`downbeats` en tiempo FINAL (el de la pista). Sincrono (10-40 s con ACE; threadpool). |
| GET `/music-file/{name}` | – | audio de la musica generada |
| POST `/captions/{job}` | `{plan?, fmt?}` | Grupos REALES de subtitulos (los que dibuja el export: `text.caption_groups` sobre las frases en tiempo final, en la caja de layout): lista `[{start, end, text, words:[{text, start, end, kw?}], lines, accent, rect:[x0,y0,x1,y1], start_final, end_final}]` en tiempo ORIGINAL (salvo `*_final`); `words` = palabras del plan (texto sin el cambio de mayusculas del preset); `rect` normalizado a la caja 9:16. `[]` con subtitulos apagados. Cacheado por contenido; cabecera `X-Captions-Ms`. 400 si el plan no es v2. |
| POST `/translate/{job}` | `{plan?, lang? ("en"), texts?, spans?, phrases?}` | Traduce con el LLM local para editar en vivo (NO se guarda: el Studio lo pone en el plan). `lang` en `es` (identidad), `en`, `pt` (portugues de Brasil), `fr`, `de`, `it` (otro -> 400); el idioma se le nombra al modelo en su propia lengua. Con `texts` [str]: solo esos textos -> `texts_<lang>` 1:1 (titulos y graficos del plan v2): cada titulo se traduce COMPLETO (sus lineas `\n` son partes de una frase) y vuelve con el MISMO numero de lineas y sus MAYUSCULAS; contexto = la frase hablada que lo cubre (`spans` [[start, end]] en tiempo original, o la busqueda del titulo en la transcripcion; solo en titulos de 4+ palabras). Lo que no respeta las lineas, sale en otro idioma (gallego, espanol) o tradujo tambien el contexto se reintenta estricto y sin contexto. `untranslated` = indices que quedaron iguales al original (lo neutro, como precios, no cuenta). `phrases:true` agrega los subtitulos. Sin `texts`: subtitulos del plan -> `phrases_<lang>` (mismo tramo por frase, palabras repartidas; `phrases_untranslated`) y titulos de highlights -> `titles_<lang>`. `{lang, ms, ...}`. **503** con el motivo si la IA local no tradujo NADA (Ollama caido o sin modelo): nunca devuelve el espanol como traduccion. |
| POST `/analysis/{job}` | `{plan?, fmt?}` + opciones del proyecto (opcionales) | `{ok, started}`: (re)lanza el analisis de fondo |
| POST `/takes/{job}` | `{sensitivity: "suave"\|"normal"\|"a_fondo", use_llm?: true, base_rev?, fmt?}` (alias `conservador`/`agresivo`) | `{plan_rev, takes, cuts}` (`takes` = `plan.takes`, `cuts` = los `src "takes"`; seccion 3.3). Re-detecta sobre las palabras del plan GUARDADO (bordes con `plan.wav` si existe), reemplaza las tomas que el usuario no toco y conserva silencios, manuales, "ai" y `user: true`. El tema (LLM) solo se pide si el plan no lo tiene. Con `base_rev` viejo, o si otro escritor gano mientras corria -> **409** `{ok:false, plan_rev}`. Sin cambios, misma revision. Relanza el analisis de fondo con debounce (`REELFY_TAKES_KICK_S`, 5 s: una rafaga de cambios = un analisis). 400 sensibilidad o base_rev invalidos; 404 job; 409 sin plan. `GET /settings` anuncia `features: ["takes", ...]` y `GET /jobs` trae `takes: {n, enabled, removed_s}` (segundos de mas sobre los silencios) por proyecto. |
| POST `/cancel/{job}` | `?only=preview\|analysis\|direct\|music\|job` | sin `only`: cancela render/analisis del job y sus tareas laterales; con un EXPORT en curso cancela el export, el analisis de fondo y re-dirigir pero NO la vista previa (ni su actualizacion) ni la musica (son de la edicion). Con `only`: SOLO esa tarea lateral (cancelar la vista previa no mata un export en curso) o solo el trabajo principal (`job`). 400 con otro valor. |
| GET `/source/{job}` | `?proxy=1` | Video del trabajo para el Studio. Con `proxy=1`: proxy H.264 con el lado corto a 720 (nunca agranda), GOP de ~1/3 s (seeks rapidos), MISMO reloj que el original (el Studio sigue en tiempo original), color bt709 (HDR con el tonemap del render) y audio AAC; se genera en segundo plano al llegar a `review`, al abrir un proyecto viejo (`GET /plan`) o con `POST /analysis` (tarea lateral `proxy`: `proxy_state/pct/url` en `/status`; prioridad baja `nice -n 10` para no quitarle CPU al analisis de fondo). Mismo reloj EXACTO: `-enc_time_base demux` conserva el pts de cada cuadro (±1 ms). Si aun no esta sirve el original y lo genera; cabecera `X-Reelfy-Proxy: ready\|pending\|none` (`none` = no habra proxy: p. ej. ya fallo para esta fuente, con el mismo tamano y fecha no se reintenta). |
| GET `/jobs` | `?limit=` (20, max 50) | Proyectos recientes sin deserializar planes: `[{id, kind, state, phase, pct, message, error, created, updated, ext, plan_rev, has_plan, version, topic, duration, final_duration, style, shots, thumb}]` (`topic` = tema del director o el primer titulo; `final_duration` aproximada: duracion menos cortes; `thumb` = `/thumb/{id}` o null; null tambien con un trabajo en error sin plan o cuya miniatura ya fallo). |
| GET `/thumb/{job}` | – | miniatura JPEG (alto 360) del video del trabajo, ~1 s dentro del primer tramo conservado; se genera una vez (pedidos simultaneos esperan al primero). Un video que no se decodifica deja una marca y responde **404** sin relanzar ffmpeg (detalle generico, sin rutas). |
| GET `/settings` | – | `{pexels, pro_opts: ["logo","enhance_audio"], pro_opts_optin: ["air"], logo: bool, pro_langs: [es,en,pt,fr,de,it], features: [captions, source_proxy, jobs_meta, music_beats, translate_texts, translate_titles, preview_music_refresh, music_sync, air_optin, media, profile]}`: `pro_opts` = opciones del proyecto que el render pro aplica siempre (el Studio habilita esos controles); `pro_opts_optin` = las que aplica SOLO si el cuerpo lo pide explicitamente (el aire con `air_user: true`) |
| GET `/brand/logo` | – | logo de la marca (PNG) o **204** sin cuerpo si no hay (consola del Studio limpia). `POST /brand/logo` (multipart `logo`) lo reemplaza |

Opciones del proyecto (no viven en el plan; el Studio las manda en el cuerpo de `/frame`,
`/preview` y `/render`): `logo` `{pos: tl|tr|bl|br, size (0.04-0.4 del ancho de la caja),
opacity}` o null (usa el logo de la marca), `air` (0-0.35: margen desenfocado; OPT-IN: solo
cuenta con `air_user: true`, es decir, cuando el usuario movio el control; el default del Studio
AIR=0.16 ya no lo activa y el render pro va a sangre completa, el look profesional de un talking
head horizontal en 9:16. Ademas SOLO cambia la imagen con una fuente mas ANCHA que la salida: con
otra fuente el servidor lo descarta para que el video base y sus caches no cambien) y
`enhance_audio` (default true; false = voz sin la cadena de estudio). Sin las claves: audio de
estudio, sin aire ni logo. El servidor recuerda las EFECTIVAS del ultimo pedido por trabajo
(`/status` `engine`: {air?, air_user?, enhance_audio, logo}; compara lo efectivo: un aire que no
aplica a la fuente no relanza nada) y el analisis de fondo las usa (el aire y el audio de estudio
cambian el video base y la voz). Un aire guardado por versiones anteriores (sin `air_user`) no vale.

Musica IA de la edicion (tarea lateral `music`): UNA generacion ACE por trabajo y siempre la de
la edicion vigente (re-dirigir, vista previa, analisis de fondo): otra pista CANCELA la anterior
(mata ace-synth); una pista ya en cache no genera nada. El analisis de fondo ya no la precalienta
en el motor (`opts.prefetch_music=False`: aquel hilo no se podia cancelar y cada cambio de estilo
dejaba una generacion huerfana). `/status`: `music` y `music_state` (generating/ready/syncing/
synced/superseded/canceled/error), `music_pct`, `music_message`, `music_resync` ("available" =
la pista esta en cache pero la propuesta no quedo sincronizada porque el usuario la edito:
re-dirigir sincroniza al instante), `music.plan_rev` (la revision sincronizada). Al terminar una
pista pedida por re-dirigir, el servidor repite la MISMA peticion (plan original, estilo,
intensidad, semilla) con sincronia (< 1 s) y la guarda solo si el plan guardado sigue siendo la
propuesta (`update_plan` con `base_rev`). `?only=music` la cancela.

Medios propios (imagen o clip subido por `POST /sources` y referenciado en el plan como
`{sid, type, ...}`): el servidor pasa `opts.media_root` = `DATA/input/sources` y el motor
resuelve cada `sid` a `<media_root>/<sid>.<ext>` (nunca una ruta del plan). La limpieza del disco
no borra una fuente que un plan referencia (`"sid"` en cualquier item).

`/status/{job}` es LIVIANO: `phase, state, kind, pct, message, eta, elapsed, stage, warnings,
plan_rev, has_plan, clips, error` + tareas laterales (`preview`, `analysis`, `direct` como dict
y aplanadas `<tarea>_state/pct/file/stage/error/done/t/message/music_pending/url/resync/prev_file`;
tambien `proxy`, `music` y `engine`). El plan completo va solo con
`?plan=1`, con `?rev=N` si `plan_rev > N`, o (UI legacy sin parametros) cuando cambia la fase;
`?plan=0` nunca. `stage`: compose, analyze, director, review, timeline, base, analysis,
audio, render, mux, done.

Analisis de fondo: tras `review` (y tras re-dirigir) el servidor lanza
`render.ensure_analysis(plan, fmt + aire/audio de estudio del proyecto)` en segundo plano (tarea
`analysis`, cancelable, uno a la vez). Su clave (`key`/`bkey` en `/status`; `render.analysis_key`
del motor) solo cuenta lo que cambia el analisis: `bkey` = base y voz (video, cortes,
reencuadre, formato, aire, audio de estudio); `key` = `bkey` + rostro, rangos de matte y
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

Disco: el servidor purga los archivos de los trabajos viejos (conserva 50; con ellos su proxy
`work/<job>_src720.mp4` y su miniatura), deja 2 exports por trabajo, borra huerfanos y fuentes
sin uso (> 24 h; una fuente que un plan usa como medio propio cuenta como usada) y pone tope LRU
de 5 GB a las caches
regenerables (`work/motion_cache`, `work/music`); con < 5 GB libres baja el tope y conserva
solo los 5 trabajos mas recientes.

## 7. Calidad y pruebas
- `pytest spike/tests` en verde. Cada modulo trae tests de su logica y una demo visual.
- E2E: `spike/tests/e2e_pro.sh` corre compose -> analyze -> direct -> render con los clips de
  prueba y genera hoja de contacto + metricas (duracion, LUFS, tiempo de render, % cuadros con
  evento visual, texto dentro de SAFE).
- Barra de calidad: comparacion visual contra `ejemplo_objetivo.mp4` (revision humana de Kevin).
