# Reelfy — Arquitectura y stack (estado actual)

**Última actualización:** 2026-09-27 · **Versión:** v0.5.0 (Motion Engine)

Reelfy es un **editor de video corto con IA para desktop** (Win/Mac/Linux) que corre
**100% local y offline**. Convierte un video largo en **shorts verticales listos para
publicar**: subtítulos ES sincronizados palabra por palabra, cortes de silencios,
reencuadre que sigue tu cara, audio de estudio, resaltado de keywords + emojis, y
opcionalmente B-roll. Con un **editor de línea de tiempo profesional** como primer paso.
Desde v0.5 el **Motion Engine** edita solo al nivel de un editor profesional de short-form
(tipografia animada, graficos, escenas, efectos de camara y de sujeto, transiciones, SFX,
musica generada localmente) y todo queda editable en el Studio (ver seccion 6 y
`docs/MOTION-ENGINE.md`).

> Tesis: costo por usuario ≈ $0 (todo el cómputo pesado es open-source y local). El
> diferenciador defendible es la **calidad de ejecución** — captions ES con timing
> preciso + estilizado listo para publicar — no la automatización básica (commodity).
> Ver `docs/roadmap.md` para el veredicto de investigación y las decisiones de alcance.

---

## 1. El "paquete fundamental" (stack)

| Capa | Tecnología | Rol |
|------|-----------|-----|
| Cáscara desktop | **Tauri 2** (Rust + WebView) | App nativa Win/Mac/Linux; empaqueta el motor Python |
| Motor / servidor | **FastAPI + uvicorn** (en proceso) | API local `http://localhost` que orquesta el pipeline |
| UI | HTML/CSS/JS de un solo archivo (`spike/app/static/index.html`) | Editor + previsualizador **canvas en tiempo real** |
| Motor de video | **FFmpeg** | Columna vertebral: corte, escala, fades, crossfades, capas, quema de subtítulos ASS |
| Visión | **OpenCV** | Runtime de detección facial y procesamiento de frames |
| Audio DSP | **librosa / soundfile / scipy / soxr** | Análisis de audio para el alineador |
| LLM embebido | **ollama** (binario + runners, >= 0.30) | Sirve el modelo de lenguaje y vision local (fuera del `~/.ollama` del sistema) |
| Compositor (v0.5) | **skia-python** (BSD-3) + numpy, N procesos | Render por cuadro del Motion Engine (texto, graficos, escenas, efectos) |
| Inferencia | **onnxruntime** (MIT) | Alineador, recorte de sujeto (MODNet/PP-HumanSeg), YuNet via OpenCV |
| Musica IA (v0.5) | **acestep.cpp** (MIT, GGML: Metal en mac, CPU/Vulkan en win/linux) | Genera la cama musical local (ACE-Step 1.5); fallback procedural propio |

Todo el motor se instala sobre un **python-standalone** al ensamblar la app
(scripts `assemble-engine-{linux.sh,macos.sh,windows.ps1}`). Dependencias fijadas en
`spike/requirements.txt`.

---

## 2. Las IAs / modelos y para qué sirve cada uno

### 2.1 Transcripción — `whisper.cpp` con `ggml-large-v3-turbo` (~1.6 GB)
`spike/scripts/pipeline.py::transcribe()`. Voz → texto con **timestamps por palabra**
(español, `-l es`, `-ml 1 -sow`, DTW `--dtw large.v3.turbo`). Corre en **GPU Metal**
en Apple Silicon con **fallback automático a CPU**. El campo "nombres propios/marcas"
del UI se pasa como `--prompt ... --carry-initial-prompt` para escribir bien marcas.

### 2.2 Sincronía de captions — alineador `MMS_FA` (wav2vec2 CTC) en **ONNX int8**
`spike/scripts/align.py`, modelo `spike/models/mms_fa_int8.onnx`, corre con
**onnxruntime** (sin PyTorch). **Este es el _wedge_ / la ventaja de Reelfy**: clava el
timing de cada palabra (mediana ~6 ms) para el efecto karaoke. Se exportó de PyTorch a
ONNX int8 → misma calidad, 1.7× más rápido, ~1.5 GB menos de instalador, e igual en
Windows (antes degradado). Export: `scripts/export_aligner_onnx.py`; validación:
`scripts/validate_aligner.py`.

### 2.3 LLM local — `qwen3.5:4b` (Apache-2.0) sobre `ollama` embebido
El cerebro de decisiones (env `REELFY_LLM_MODEL`, default `qwen3.5:4b`, texto + vision;
endpoint `REELFY_OLLAMA_URL`). Todas las llamadas usan `think: false` (con razonamiento
activo el modelo devuelve la respuesta vacia en modo JSON). Usos, **todos degradan con gracia
a heurística local** si el LLM no está disponible:
- **Highlights** (`scripts/highlights.py`): elige los mejores fragmentos → shorts (el "nº de shorts" que pides).
- **Resaltado inteligente** (`scripts/captionsmart.py`): por frase, qué keywords resaltar + qué emojis poner (con densidad configurable).
- **Traducción** (`scripts/translate.py`): subtítulos ES→EN/PT/FR/DE/IT manteniendo el timing, en chunks para no desbordar el contexto; titulos del plan v2 completos (sus lineas son partes de una frase) con el mismo numero de lineas y la frase hablada como contexto. El idioma destino se nombra en su lengua ("português do Brasil": nombrado en espanol el modelo mezclaba gallego) y una salida con palabras de otro idioma se reintenta. Sin la IA, `/translate` responde 503 (nunca devuelve espanol como traduccion).
- **Director** (v0.5, `scripts/motion/director.py`): describe cada toma (VLM) y entiende el
  guion (estructura, entidades, enfasis) para coreografiar la edicion.
- **Perfil de la persona** (`scripts/motion/profile.py`, `POST /profile/parse`): del texto libre
  ("ella es la Dra. X, es odontologa, su instagram es @x y agenda en paseo.club/...") saca nombre,
  profesion con su genero y negocio (reglas + LLM validado contra el texto); los enlaces (redes,
  reservas, web, WhatsApp, mapa) se reconocen sin IA.
En la app, una instalacion NUEVA baja `qwen3.5:4b` (~3.4 GB) en el setup obligatorio. Al
**actualizar de v0.4** (ya hay `qwen2.5:3b` en el ollama embebido) la app NO se bloquea: sigue
con `qwen2.5:3b` (solo texto; la vision del director queda en heuristicas porque
`REELFY_VLM_MODEL` apunta al modelo nuevo mientras falta) y baja `qwen3.5:4b` en segundo plano
como opcional (`optional.llm`, reanudable, con progreso). Al quedar listo lo activa en caliente
(`REELFY_LLM_MODEL`, `highlights.MODEL`) y borra el viejo cuando no hay trabajos en proceso.
Sin red la app sigue funcionando con el modelo viejo o con heuristicas. Verificado: el ollama
0.30.11 de v0.4 y el 0.34.2 actual sirven `qwen3.5:4b`.

### 2.4 Reencuadre que sigue al sujeto — `YuNet` (detección facial ONNX)
`spike/scripts/reframe_track.py`, modelo `models/face_detection_yunet_2023mar.onnx`
(**Apache-2.0**, no YOLO/AGPL — importante legalmente). Una "cámara virtual" amortiguada
que detecta la cara y **panea el recorte 9:16** para seguir al sujeto, en vez de un
center-crop fijo.

### 2.5 Audio de estudio — `RNNoise` + cadena FFmpeg
`pipeline.py` cadena local: `highpass` → **RNNoise** (`arnndn`, `models/rnnoise.rnnn`,
mix 0.85) → de-esser → compresión suave → **loudness EBU R128 a -14 LUFS** (estándar de
redes) → resample 48 kHz.

### 2.6 Corte de silencios — `auto-editor` + ffmpeg `silencedetect`
`spike/scripts/edit.py`: quita silencios → jump-cuts snappy; punch-in zoom en énfasis.

### 2.7 B-roll automático — **Pexels Videos API** *(única pieza en la nube, opcional)*
`spike/scripts/broll.py`: corta a metraje de stock según lo que dices (busca en inglés,
Pexels indexa mejor). Requiere API key (`DATA/settings.json` o `REELFY_PEXELS_KEY`).
**Todo lo demás funciona sin internet.**

> Nota: `PySceneDetect` está en `requirements.txt` pero **no se usa activamente** hoy
> (el corte real lo hacen auto-editor + ffmpeg). Candidato a podar del instalador.

---

## 3. El pipeline (de video crudo a shorts)

```
video(s) ──▶ [editor de línea de tiempo]  ← PRIMER paso obligatorio (montaje pro)
                     │  (recorta, reordena, capas, fades, transiciones, volumen)
                     ▼
              /compose (FFmpeg) ──▶ composición única
                     ▼
   ffmpeg audio ──▶ whisper.cpp (word ts) ──▶ MMS_FA (sincronía) ──▶ captions ASS
                     ▼
   qwen2.5 highlights ─ captionsmart ─ (translate) ──▶ PLAN editable
                     ▼
   YuNet reframe ─ auto-editor cortes ─ RNNoise/loudnorm ─ (Pexels B-roll)
                     ▼
              PREVIEW en tiempo real (canvas) ──▶ ajustes ──▶ EXPORT 9:16/1:1/4:5/16:9
```

Endpoints clave (`spike/app/server.py`): `/sources` (subida multi-archivo),
`/compose` (monta timeline → analiza con IA: `glossary`→subtítulos, `highlights`→nº
shorts), `/analyze` (ruta rápida un-video), `/status/{job}`, `/setup` (estado del
motor/modelos), `/tracks` (catálogo de música), `/brand/logo` (204 sin logo), `/settings`
(capacidades: `pro_opts`, `pro_langs`, `features`), `/jobs` (proyectos recientes con tema,
duracion, version y miniatura `/thumb/{job}`). Los del motor pro (`/direct`, `/frame`,
`/preview` pro, `/render` v2, `/music`, `/captions`, `/translate`, `/source?proxy=1`, `/fx/*`,
y el perfil de la persona: `/profile/parse`, `/profile/platforms`, `/profiles`, `PUT /profile/{job}`)
estan en `docs/MOTION-ENGINE.md` seccion 6.1.

---

## 4. Editor de línea de tiempo profesional (v0.4.0)

Es un NLE por capas de verdad, en `index.html` (funciones `asm*`):
- **Modelo por posición libre**: cada clip tiene `start` y `layer`; se permiten huecos (negro al exportar).
- **Capas de video**: los clips no se enciman; arrastrar vertical mueve/crea capas (cutaway = capa superior full-frame).
- **Magnetismo con prioridad + resistencia** (histéresis 9/16 px): bordes de clip (3) > playhead (2) > imágenes/overlays (1).
- **Recorte por bordes**, división, arrastre libre, snapping, zoom (mínimo = todo el timeline visible).
- **Fades** (audio+video), **transiciones** (crossfade), **volumen/mute** por clip.
- **Deshacer/rehacer**, atajos de teclado, scrubbing en la regla.
- **Previsualizador canvas en tiempo real**: captions (base/highlight/keyword, karaoke/box, mayúsculas, outline, sombra, margen, animación, emojis), logo, marcadores de B-roll.
- Composición backend en `spike/scripts/compose.py` (normaliza segmentos → concat/xfade, capas full-frame vía `setpts`+`overlay`+`enable`, PiP/stickers).

---

## 5. Empaquetado y distribución (release)

- **Build**: al taguear `reelfy-v*` en `danpez/reelfy` (privado), el workflow
  `.github/workflows/reelfy-desktop.yml` compila whisper.cpp, ensambla el motor y corre
  `tauri build` en **Linux (.deb)**, **Windows (NSIS `*-setup.exe`)** y **macOS (.dmg
  firmado + notarizado)**. Publica un GitHub Release en el repo privado.
- **Distribución pública**: el sitio (`danpez/reelfy-site`, Vercel) baja desde el repo
  **público** `danpez/reelfy-releases` con nombres limpios: `Reelfy-Setup.exe`,
  `Reelfy.dmg`, `Reelfy.deb` (apuntando a `releases/latest/download/...`). El modelo del
  alineador se aloja ahí también (`models-v1/mms_fa_int8.onnx`).
- **Primer arranque**: la app descarga los modelos una sola vez a un directorio de datos
  escribible. Obligatorio (~5.4 GB): whisper turbo (1.6 GB) + LLM `qwen3.5:4b` (3.4 GB) +
  afinador (0.36 GB) + sujeto (32 MB). Opcional en segundo plano, sin bloquear: musica IA
  ACE-Step (~3.6 GB; si falta o no hay espacio, musica procedural) y, al actualizar de v0.4,
  el LLM nuevo (ver 2.3). El chequeo de espacio exige lo que falte + 2 GB de margen (y la
  musica, + 3 GB); las caches regenerables tienen tope propio (ver "Disco").
- **Disco** (recoleccion de basura del servidor, `disk_gc`): al arrancar, tras cada export y
  al crear trabajos. Trabajos purgados (se conservan los 50 mas recientes) -> se borran sus
  archivos (entrada compuesta, exports, vistas previas, musica escuchable, audio y
  transcripcion del analisis); de cada trabajo solo quedan los 2 exports mas recientes (con
  shorts, miniaturas y stats); archivos huerfanos (> 1 h) y fuentes subidas que ningun trabajo
  usa (> 24 h) se borran; `work/motion_cache` + `work/music` tienen tope LRU de 5 GB
  (`REELFY_CACHE_GB`; nada usado en los ultimos 10 min se toca). Con menos de 5 GB libres
  (`REELFY_MIN_FREE_GB`) el tope baja y se purgan trabajos viejos hasta dejar los 5 mas
  recientes.
- **Motor ensamblado** (`reelfy-desktop/scripts/assemble-engine-*`): codigo, `assets/music`,
  `assets/motion` (fuentes, iconos, SFX), YuNet + RNNoise, whisper-cli, **acestep.cpp**
  (`bin/acestep/`: ace-synth, ace-server y su libggml en carpeta propia, porque whisper trae
  otra version con los mismos nombres), ffmpeg, ollama y python-standalone con
  `spike/requirements.txt` (incluye `skia-python`). Cada script termina con un humo
  `import skia, onnxruntime, cv2` que es **FATAL** en las 3 plataformas (render.py importa
  skia al cargar: sin esas libs /render pro, /frame y /preview mueren, asi que el build no
  puede salir en verde) y uno de `ace-synth --help` que solo avisa (la musica IA es opcional). El CI compila acestep.cpp a un commit fijo: Metal en mac;
  en Windows/Linux backends cargables (`GGML_BACKEND_DL` + variantes de CPU, Vulkan si hay
  SDK/glslc). whisper se compila con `GGML_NATIVE=OFF` fuera de mac (sin "illegal
  instruction" en CPUs distintas a la del runner).
- El `.deb` declara `libegl1, libgl1, libfontconfig1, libvulkan1` (skia y Vulkan).

### Notas por plataforma
- **Windows**: NSIS, 64-bit. SmartScreen puede advertir (instalador aún sin firma
  digital) → "Más información" → "Ejecutar de todas formas". Se podan runners GPU de
  ollama (CUDA/ROCm) y torch/torchaudio (NSIS no soporta >2 GB).
- **macOS 13+ / Apple Silicon**: firmado + notarizado (abre sin advertencias). DMG de
  marca vía dmgbuild.
- **Linux**: Ubuntu 22.04+/Debian, `sudo apt install ./Reelfy.deb`.

---

## 6. Motion Engine (v0.5): edicion pro automatica y editable

Contrato normativo: `docs/MOTION-ENGINE.md`. Paquete `spike/scripts/motion/`.

```
/compose o /analyze ─▶ analisis (whisper + MMS_FA + LLM) ─▶ plan v1 (+ shots reales)
      ─▶ director.direct (VLM tomas + LLM guion + coreografo) ─▶ plan v2 en `review`
      ─▶ analisis de fondo (render.ensure_analysis: base, rostro, sujeto, color en cache)
Studio: /frame (cuadro exacto) · /preview pro (draft 540p con audio) · /direct · /music
      ─▶ /render: render.render_pro (base ─ analisis ─ audio ─ compositor ─ mux ─ shorts)
```

- **Plan v2**: cada decision de la IA es un item con `enabled` y parametros (texto, graficos,
  escenas, efectos, transiciones, SFX, musica, color); el Studio lo edita y el render lo
  respeta. Sin LLM/VLM el director usa heuristicas; nunca tumba el analisis.
- **Perfil** (`plan.profile`): nombre, profesion, negocio y enlaces de quien sale en el video
  (perfiles reutilizables en `DATA/assets/brand/profiles.json`). Manda sobre lo que el director
  infiere (nunca el nombre de un letrero): rotulo con nombre + profesion (+ negocio si cabe),
  Seguir con la red y el @usuario (foto de perfil o iniciales), pastilla de reservas con su enlace
  corto cuando la voz invita de verdad ("link aqui abajo", "agenda", "pide tu cita"), y tarjeta
  final >= 2.2 s con la pila alineada (ubicacion, Seguir, chips de redes, reservas) o, si a camara
  no cabe, una escena corta con el video desenfocado. `PUT /profile/{job}` lo cambia y re-dirige
  al instante sin re-analizar (tambien se re-aplica si el autoguardado `PUT /plan` trae otro perfil).
- **Render pro**: video base cacheado (reencuadre, tonemap HDR, cortes), pista de rostro
  (YuNet), recorte de sujeto (MODNet) solo donde se pide, compositor Skia en N procesos,
  mezcla voz + musica + SFX a -14 LUFS / -1.5 dBTP, H.264 bt709 y shorts de los highlights.
- **Montaje** (`scripts/compose.py`): cada clip se normaliza (autorrotacion, tonemap HLG/PQ ->
  bt709, 30 fps, audio 48 kHz) de a 2 en paralelo y se unen con concat (o xfade). Un lienzo
  VERTICAL se limita a 1620x2880 (1.5x la salida 9:16: el base del render es 1080x1920 y la
  camara hace zoom sobre el base), ~33 % menos CPU que a 4K; lo horizontal no se limita (el
  recorte 9:16 necesita su altura). Los limites reales de cada clip van a `plan.shots`.
- **Progreso**: `stage` por etapa + `pct` global monotono + ETA. Analisis: montaje 2-32 %,
  transcripcion/IA 34-70 % (2-70 % sin montaje), director 70-99 % (tomas, guion, coreografia,
  musica con su tramo propio; en pasos largos sin sub-progreso la barra avanza sola). Export
  por etapa; cancelable en cualquier etapa (mata ffmpeg y procesos): un export cancelado
  vuelve a `review` (el plan sigue valido). Vista previa real, analisis de fondo y re-dirigir
  en segundo plano son tareas laterales del mismo proyecto (no cambian su fase; se cancelan
  por separado con `/cancel/{job}?only=`).
- **Analisis de fondo**: su clave solo cuenta lo que lo cambia (video, cortes, formato,
  rostro, rangos de matte, color): editar textos, graficos o SFX no lo cancela; la vista
  previa y el export esperan y reutilizan el que esta en curso con la misma base.
- **Musica elegida por el usuario** (fuente procedural/biblioteca/ninguna o prompt, bpm o
  semilla propios): entra ANTES de sincronizar la coreografia; el director no genera ni
  sincroniza a la pista IA (`music_sync=False`) y re-dirigir conserva esa eleccion. Re-dirigir
  sin `full` (cambio de estilo, regenerar) nunca espera a ACE: con la pista en cache sincroniza
  en la misma respuesta (< 1 s); una pista nueva la genera la tarea lateral `music` y al
  terminar el servidor guarda la propuesta sincronizada a los beats (si nadie la edito; si no,
  `music_resync: available`).
- **Una generacion de musica IA por trabajo** (tarea lateral `music`): siempre la de la edicion
  vigente; otra pista cancela la anterior (mata ace-synth). El analisis de fondo y la vista
  previa ya no la precalientan en el motor (aquel hilo no se podia cancelar: cuatro cambios de
  estilo dejaban cuatro generaciones de 40-55 s, todas obsoletas menos una). La vista previa
  que sono con la musica provisional espera a esa tarea y se rehace sola; la provisional queda
  servible (`prev_file`) hasta la siguiente vista previa.
- **Procesos hijos**: el compositor usa procesos `spawn`; el servidor oculta su `__file__` al
  arrancar y no ejecuta nada si se reimporta como `__mp_main__` (un hijo nunca corre
  `recover()` ni workers).
- **Opciones del proyecto en el render pro**: logo de la marca (esquina, tamano, opacidad),
  aire (margen desenfocado, solo con fuentes mas anchas que la salida y OPT-IN: solo con
  `air_user: true`; el default va a sangre completa, `/settings.pro_opts_optin`) y audio de estudio viajan
  con `/frame`, `/preview` y `/render` y el motor los aplica igual en cuadro exacto, vista previa
  y export; el servidor recuerda las del ultimo pedido (o las que llegan con `/compose` o
  `/analyze`) y el analisis de fondo las usa (el aire y el audio de estudio cambian el video
  base y la voz; con una fuente vertical el aire se descarta y las caches se comparten).
- **Studio rapido**: proxy H.264 720p del video del trabajo (`/source?proxy=1`, GOP ~1/3 s, mismo
  reloj exacto, prioridad baja, generado en segundo plano al llegar a review o al abrir un
  proyecto viejo; una fuente que no decodifica no se reintenta) para seeks rapidos; grupos REALES de
  subtitulos (`/captions`, los mismos del export) para que la vista en vivo agrupe igual; la
  vista previa real que sono con musica provisional (IA aun generandose) se rehace sola con la
  pista IA y publica el archivo nuevo.
- **Medios propios**: imagenes o clips subidos por `/sources` y usados en el plan (`{sid}`); el
  motor los resuelve en `DATA/input/sources` (`opts.media_root`, nunca rutas del plan) y la
  limpieza del disco no borra una fuente que un plan referencia.
- **App de escritorio**: ventana de 1340x940 con minimo 1200x760 (por debajo el lienzo 9:16 y
  las pistas del Studio no caben), ajustados al area util del monitor (en 1280x800 el minimo
  mas la barra de titulo no cabia); `cargo test --lib` lo prueba en el CI.

### 6.1 Modelos, assets y licencias (todo local)

| Pieza | Licencia | Donde / cuando |
|---|---|---|
| Qwen3.5 4B (`qwen3.5:4b`, texto + vision) | Apache-2.0 | ollama embebido; setup |
| whisper `ggml-large-v3-turbo` | MIT | setup (1.6 GB) |
| MMS_FA (alineador, export ONNX int8 propio del checkpoint MMS de Meta) | **A revisar**: los pesos MMS se publicaron como CC-BY-NC 4.0 (no comercial); confirmar antes de vender | setup (0.36 GB) |
| MODNet (Xenova/modnet ONNX) | Apache-2.0 | `DATA/models/modnet.onnx`; setup (26 MB, revision fija + SHA-256) |
| PP-HumanSeg (opencv_zoo) | Apache-2.0 | `DATA/models/matte/`; setup (6 MB) |
| YuNet (deteccion facial) | Apache-2.0 | en el instalador |
| ACE-Step 1.5 (GGUF: DiT turbo + VAE; codificador Qwen3-Embedding 0.6B) + acestep.cpp | MIT (codificador Apache-2.0) | `music-models` (~3.6 GB, opcional); binario en el instalador |
| Fuentes (Poppins, Montserrat, Inter, Anton, DM Serif, Caveat, ...) | SIL OFL 1.1 (algunas Apache-2.0) | `spike/assets/motion/fonts` (licencias incluidas) |
| Iconos Tabler | MIT | `spike/assets/motion/icons` |
| SFX (32 sonidos, variantes) | propios, sintetizados por `motion/sfx.py` | `spike/assets/motion/sfx` |
| Musica procedural | propia (`motion/music.py`) | sin descarga |
| skia-python / Skia | BSD-3 | requirements |

### 6.2 Pruebas
- `pytest spike/tests` (unidad + integracion). Del servidor: `test_server_api.py` levanta un
  servidor real aislado con un video sintetico y recorre la API 6.1 (incluye: editar un texto
  no cancela el analisis, cancelar la vista previa no mata el export, export cancelado ->
  review, PUT /plan con base_rev, /music tras el render es acierto de cache con beats, /direct
  en segundo plano, logo en el cuadro exacto, /captions igual al render, proxy 720p con GOP
  corto y mismo reloj exacto, fuente que no decodifica sin reintentos, miniatura con pedidos
  simultaneos, /jobs con miniatura, /translate 503 sin la IA, aire opt-in en una fuente
  horizontal); `test_translate.py` (titulos con sus lineas, gallego/contexto colado,
  Ollama caido, endpoint con IA falsa); `test_server_setup.py` (actualizacion de
  v0.4 con un ollama falso, musica elegida antes de sincronizar, barra monotona, reanudar la
  musica IA, recoleccion de basura (tambien fuentes usadas por un plan), clave del analisis con
  aire/audio de estudio (opt-in, comparacion efectiva), vista previa que se rehace con la
  musica IA sin borrar la provisional, re-dirigir con una generacion por trabajo y sincronia
  automatica, cancelar el export sin tocar la vista previa, hijo spawn que reimporta el
  servidor); `test_jobstore.py` (tambien el resumen del plan para /jobs);
  `test_compose_shots.py` (limites, lienzo, montaje en paralelo).
- E2E real: `spike/tests/e2e_pro.sh [puerto] [datos]` (o `REELFY_E2E=1 pytest
  tests/test_e2e_pro.py`): sube los clips crudos de prueba, compone, dirige, pide cuadros,
  vista previa y export con shorts, y valida duracion, 1080x1920, -14 LUFS con true peak
  <= -1.0, musica en la mezcla y nada omitido, barra monotona (y que el montaje avance),
  orientacion (cabeza y pies como en la fuente), overlays, que `/frame` coincida con el
  export, el logo de marca en el export (y que el aire no rehaga el base de una fuente
  vertical), `/captions`, el proxy 720p, `/jobs` + miniatura, `/music` con beats, `/translate`
  de titulos a pt y de (traducidos, mismas lineas, sin gallego ni espanol), si la vista previa
  sono con musica provisional que se rehaga con la IA sin borrar la provisional, un cambio de
  estilo que queda sincronizado solo con la musica IA nueva y una fuente HORIZONTAL sintetica
  (flujo real) donde el default va sin aire. Avisa
  (sin fallar) si el VLM cayo a heuristicas o si los recortes de la IA eliminaron una toma
  completa. `--label frio|caliente` para reportar tiempos por separado. Con
  `REELFY_PLAYWRIGHT` (ruta de playwright-core) corre despues la E2E del Studio en Chrome real
  (`tests/studio_smoke.mjs`) sobre el mismo trabajo. Salidas en `spike/work/e2e/` (ignorado
  por git).

