# Reelfy — Spike de validación (M2 Max)

**Objetivo:** validar (o negar) con datos el único wedge defendible de Reelfy → **¿podemos generar captions en español con timing/sincronía medible­mente mejores que OpusClip/Submagic, y un vertical "listo para publicar" en un solo paso, corriendo 100% local en Apple Silicon?**

Esto NO es el producto. Es un experimento acotado (time-boxed) para decidir go/no-go de **ejecución** antes de construir features.

## Hipótesis a probar

1. **H1 (timing):** whisper.cpp (+ alineación) sobre audio en español produce timestamps a nivel palabra con desviación < ~120 ms respecto al audio real, de forma consistente (incl. habla rápida, acentos LATAM).
2. **H2 (pipeline local):** whisper.cpp + auto-editor + PySceneDetect + FFmpeg producen un vertical 9:16 subtitulado, en un solo comando, sin intervención manual, en tiempo razonable en M2 Max.
3. **H3 (calidad publicable):** el resultado se ve "listo para publicar" (captions legibles, sincronizados, reencuadre que no corta al sujeto).

## Métrica del wedge (timing/sync)

- **Word-level offset:** diferencia entre el inicio de cada palabra según Whisper vs. el audio real (muestreo manual de N=20-30 palabras en puntos clave, o alineación forzada como referencia).
- **Caption drift:** ¿los subtítulos se adelantan/atrasan acumulativamente a lo largo del clip?
- **Comparación:** correr el MISMO video en OpusClip/Submagic (tier gratis) y comparar el sync percibido lado a lado.
- **Criterio de éxito:** si el timing local iguala o supera a los incumbentes de forma perceptible → hay wedge. Si no → se reevalúa.

## Pipeline del spike

```
input.mp4
  │
  ├─(1) ffmpeg      → extrae audio 16kHz mono wav
  ├─(2) whisper.cpp → transcripción ES + timestamps (word-level) → JSON/SRT
  ├─(3) auto-editor → detecta/recorta silencios y muletillas
  ├─(4) scenedetect → puntos de corte por escena (metadata)
  ├─(5) LLM         → (más adelante) elige highlights sobre el transcript
  └─(6) ffmpeg      → reencuadre 9:16 + quema captions estilizados → output.mp4
```

Fase 1 del spike = pasos (1)(2)(6) — el corazón del wedge (captions con timing). Pasos (3)(4)(5) se suman después.

**Fase 2 (highlights):** el transcript se agrupa en oraciones con timestamps y un **LLM local (ollama, qwen2.5:7b)** elige los mejores fragmentos contiguos para shorts (título/gancho + razón). El pipeline renderiza el vertical completo y **corta cada highlight** en su propio short (`_short1.mp4`, `_short2.mp4`), con tracking + captions ya incrustados. Local, ~6s de selección, $0. `scripts/highlights.py`.

## Stack instalado

- **whisper.cpp** (compilado con Metal) — `spike/whisper.cpp/build/bin/whisper-cli`
- **modelo:** `ggml-large-v3-turbo.bin` en `spike/whisper.cpp/models/`
- **ffmpeg 8.1.2** (brew)
- **venv Python 3.12** en `spike/.venv/` con `auto-editor` + `scenedetect[opencv]`

## Setup (modelos, ignorados por git)

```bash
# 1) whisper.cpp con Metal
git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git
cmake -S whisper.cpp -B whisper.cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build whisper.cpp/build -j --config Release
bash whisper.cpp/models/download-ggml-model.sh large-v3-turbo   # ~1.5 GB

# 2) YuNet (subject-tracking, Apache-2.0, ~230 KB)
mkdir -p models && curl -sL -o models/face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx

# 3) ffmpeg-full (libass) + venv Python 3.12
brew install ffmpeg-full cmake
python3.12 -m venv .venv && source .venv/bin/activate
pip install auto-editor 'scenedetect[opencv]' opencv-python numpy

# 4) LLM local para highlights (Fase 2)
brew install ollama && ollama serve &   # servidor local
ollama pull qwen2.5:7b                   # ~4.7 GB
```

## Cómo correr

```bash
cd spike
./scripts/run.sh input/mi-video.mp4 \
  --glossary "Keruvin Store, Al Haramain, Oud" \  # opcional: nombres propios
  --highlights 2 \                                 # opcional: corta los 2 mejores shorts (LLM local)
  --dynamic                                        # opcional: corta silencios + punch-in zoom en los shorts
# --no-track  crop fijo centrado · --no-align  usa timing de whisper sin alineación forzada
```

Módulos: `pipeline.py` (orquesta) · `align.py` (sync) · `reframe_track.py` (cámara) · `highlights.py` (LLM) · `edit.py` (dinamismo)
```
```

## Material de prueba

Necesita un video real en español representativo del usuario meta (SMB/creador): un podcast, charla o pieza a cámara. Colocar en `spike/input/`. (Pendiente: definir el video de prueba.)

## Resultados — Fase 1 (2026-07-15)

**✅ Pipeline end-to-end validado en M2 Max.** `./scripts/run.sh input/test_landscape.mp4`:
- Cadena completa (extraer audio → whisper.cpp ES word-level → ASS word-highlight → reframe 9:16 + quema) en **~3.4 s para un video de 13 s** → **más rápido que realtime**, 100% local, costo ~$0.
- Transcripción ES **casi perfecta** (1 error menor en habla sintética); ~6.6× realtime solo en whisper con Metal.
- **Captions con resaltado palabra-por-palabra** sincronizados al timing, centrados, sin desbordamiento (fuente 78, 3 palabras/línea, wrapping). Estilo "viral" tipo Submagic/Opus. → el corazón del wedge, funcionando.
- Output 1080×1920, 30 fps. Verificado por frames.

**Gotcha resuelto:** el `ffmpeg` regular de Homebrew ya NO trae libass (render de texto); hay que usar **`ffmpeg-full`** (keg-only) → `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg`. El pipeline ya apunta ahí.

**❗ Lo que ESTO todavía NO prueba (el veredicto real del wedge):** el audio fue **sintético** (voz del sistema `say`), limpio y sin acentos/velocidad reales. La calidad de **timing/sincronía que ES el wedge** solo se puede medir con **habla real en español** (podcast/charla/a cámara) y comparándola contra OpusClip/Submagic. **Pendiente: video de prueba real de Kevin.**

Pendiente Fase 2: sumar auto-editor (silencios) + PySceneDetect (escenas) + selección de highlights por LLM; medir offset de timing con habla real; explorar alineación forzada (WhisperX) si el timing nativo no basta.

## Resultados — Prueba con video REAL (2026-07-15)

Input: `IMG_5637.MOV` — iPhone 16 Pro Max, **4K HDR (HLG 10-bit), 60 fps, 3 min, 1.3 GB**, español natural con ruido de fondo y code-switching ES/EN (demo de perfume, menciona la tienda Keruvin).

**Veredicto de ejecución (el wedge): SÓLIDO.**
- **Timing/sincronía:** en 4 palabras muestreadas en distintos puntos (incl. code-switch y nombre propio), la palabra resaltada coincidió **exactamente** con su timestamp, con la boca del hablante diciéndola en el frame → sync genuinamente ajustado sobre habla real ruidosa. Sin drift perceptible. **Esto es justo donde OpusClip acumula quejas.**
- **Transcripción ES:** muy buena en habla natural con muletillas; **falla en nombres propios** ("Keruvin"→"Kerobin", marcas) → corregible con `initial_prompt`/vocabulario custom. (Quedó quemado en el video, así que importa.)
- **Captions:** legibles, buen contraste, estilo viral, sin desbordamiento → publicable.
- **HDR:** tonemap HLG→SDR correcto (colores naturales, no lavados). Orientación auto-corregida (−180°).

**Mejoras aplicadas (2026-07-15, post-veredicto):**
- ✅ **Glosario de nombres propios** (`--glossary`): whisper `--prompt` + `--carry-initial-prompt` sesga el vocabulario. Corrigió "Kerobin"→**"Keruvin Store"** y "Outh"→"Oud" en el video real. Feature real del producto (vocabulario por marca/usuario).
- ✅ **Subject-tracking en el reframe** (`reframe_track.py`): detección de cara con **YuNet (OpenCV, Apache-2.0 — NO YOLO/AGPL)** a 4 fps → trayectoria suavizada (media móvil + clamp de velocidad) → crop 9:16 paneado por `sendcmd` que sigue al hablante. En el frame de los 45s (antes pegado al borde) ahora queda **centrado**. Pipeline con tracking: **82 s** para 3 min (incluye el paso de detección).

**Pulido de calidad (2026-07-15, ronda 3 — feedback Kevin):**
- ✅ **Sync con ALINEACIÓN FORZADA** (`align.py`, wav2vec2 MMS_FA vía ctc-forced-aligner): re-timea las palabras de whisper alineando todo el transcript al audio globalmente → corrige el drift bidireccional (<1s adelanto/atraso) que el DTW no resolvía. 392-394/~423 palabras re-timeadas; se conserva el texto original (acentos/puntuación) mapeando por secuencia normalizada. Flag `--no-align` para desactivar.
- ✅ **Tracking tipo cámara real** (`reframe_track.build_crop_x`): resorte **críticamente amortiguado** + dead-zone con histéresis (la cámara sólo re-ancla cuando te mueves de verdad, luego sostiene) → ease in/out suave, sin overshoot ni pasos. Reemplaza la interpolación lineal.
- ✅ **Aire en el encuadre**: sujeto inset (~86% del ancho) sobre un fondo del mismo frame difuminado (full-bleed, sin barras) → margen en todos los lados para que la UI de TikTok no tape contenido/subtítulos. Captions en safe zone. Constantes `AIR_SCALE`, `SUBJECT_Y`, `MARGIN_V`.
- ✅ **Highlights con payload real**: el prompt ahora exige que el fragmento ENTREGUE el valor (no sólo lo prometa) y rechaza tramos de búsqueda/relleno → el short 1 pasó de "buscando el perfume" a "las notas del perfume" (el contenido real).

**Pulido de calidad (2026-07-15, ronda 2):**
- ✅ **Sync de captions con DTW** (`--dtw large.v3.turbo`): timestamps por token vía alineación de atención cruzada, mucho más precisos que la heurística por defecto (corrige los desfases <1s).
- ✅ **Captions gapless**: cada palabra se sostiene hasta el inicio de la siguiente dentro de la frase → sin parpadeo, el resalte avanza justo en el beat.
- ✅ **Tracking fluido**: detección a 6 fps → suavizado zero-phase (media móvil sin lag) → **interpolación a nivel de frame de salida (30 fps)** con dead-zone + clamp de velocidad. Pasó de pasos discretos a **movimiento continuo (máx ~1.7px/frame @1080)**. Antes saltaba cada ~7 frames; ahora es un paneo tipo cámara virtual.

**Limitaciones restantes:**
- Tracking solo horizontal (X); pan calmado. Para varios interlocutores/cortes bruscos: segmentar por escena (PySceneDetect) y trackear por segmento.
- Verificación de fluidez/sync fino requiere VER el video en movimiento (los frames estáticos solo confirman encuadre + palabra-timestamp).
- **Render lento (RESUELTO):** el pipeline completo bajó de **6:25 → 71 s** para el mismo 3 min de 4K HDR = **5.4× más rápido**, sin pérdida de calidad. Fix: **decode por hardware (`-hwaccel videotoolbox`) + scale/crop ANTES del tonemap (tonemap a 1080×1920, ~4× menos píxeles) + encode `h264_videotoolbox`**. Solo el render pasó de ~0.52× a **3.33× realtime**. Nota: libplacebo/Vulkan NO es usable (sin runtime Vulkan/MoltenVK cargable) → la aceleración es por VideoToolbox.

**Próximas validaciones:**
- Head-to-head: subir el MISMO video a OpusClip/Submagic (tier gratis) y comparar sync lado a lado (aún pendiente — el sync interno ya se ve tight, falta el directo).
- ~~Probar `initial_prompt` con glosario (Keruvin + marcas) para nombres propios.~~ ✅ HECHO.
- ~~Subject-tracking en el reframe (detector permisivo).~~ ✅ HECHO (YuNet).
- ~~Optimizar render con VideoToolbox / downscale antes del tonemap.~~ ✅ HECHO (5.4× → 71s para 3min).
- ~~Fase 2: selección de highlights por LLM.~~ ✅ HECHO (ollama qwen2.5:7b local; de 3min → 2 shorts elegidos por IA en ~6s).
- ~~Fase 2b: dinamismo (cortar silencios + punch-ins).~~ ✅ HECHO (`edit.py`, flag `--dynamic`): `silencedetect` → concatena segmentos con voz (jump-cuts) + breathing punch-in zoom, sobre el vertical ya renderizado (captions/audio siguen en sync). Nota: en habla densa el recorte de silencios quita poco; el zoom da la energía. Tunables: `NOISE_DB`, `MIN_SIL`, `ZOOM_AMPL`, `ZOOM_PERIOD`. Alternativa futura: punch-ins discretos sincronizados a los cortes (en vez de zoom continuo).
- Head-to-head vs OpusClip/Submagic (pendiente — requiere subir el mismo video).
- Explorar alineación forzada (WhisperX) solo si aparece drift en clips más largos.
- Pendiente Fase 2b: auto-editor (cortar silencios/muletillas dentro del clip) + PySceneDetect (tracking por escena para varios interlocutores).

## Reglas heredadas del research

- Reencuadre: **NO usar YOLOv11 (AGPL-3.0)** en código que vaya a producto de pago; para el spike es aceptable, pero marcar la deuda.
- Música: baked-in (fuera del alcance de este spike inicial).
- El objetivo es CALIDAD DE TIMING, no cantidad de features.
