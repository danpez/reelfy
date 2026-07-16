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

## Stack instalado

- **whisper.cpp** (compilado con Metal) — `spike/whisper.cpp/build/bin/whisper-cli`
- **modelo:** `ggml-large-v3-turbo.bin` en `spike/whisper.cpp/models/`
- **ffmpeg 8.1.2** (brew)
- **venv Python 3.12** en `spike/.venv/` con `auto-editor` + `scenedetect[opencv]`

## Cómo correr

```bash
cd spike
./scripts/run.sh input/mi-video.mp4
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

**Limitaciones confirmadas (esperadas):**
- **Reframe center-crop sin subject-tracking:** cuando el hablante se mueve del centro, el recorte fijo lo deja pegado al borde. → hace falta seguimiento de sujeto (con detector de **licencia permisiva**, no YOLOv11 AGPL).
- **Render lento (RESUELTO):** el pipeline completo bajó de **6:25 → 71 s** para el mismo 3 min de 4K HDR = **5.4× más rápido**, sin pérdida de calidad. Fix: **decode por hardware (`-hwaccel videotoolbox`) + scale/crop ANTES del tonemap (tonemap a 1080×1920, ~4× menos píxeles) + encode `h264_videotoolbox`**. Solo el render pasó de ~0.52× a **3.33× realtime**. Nota: libplacebo/Vulkan NO es usable (sin runtime Vulkan/MoltenVK cargable) → la aceleración es por VideoToolbox.

**Próximas validaciones:**
- Head-to-head: subir el MISMO video a OpusClip/Submagic (tier gratis) y comparar sync lado a lado (aún pendiente — el sync interno ya se ve tight, falta el directo).
- Probar `initial_prompt` con glosario (Keruvin + marcas) para nombres propios.
- Explorar alineación forzada (WhisperX) solo si aparece drift en clips más largos.
- Sumar auto-editor (silencios) + PySceneDetect + selección de highlights por LLM (Fase 2).
- ~~Optimizar render con VideoToolbox / downscale antes del tonemap.~~ ✅ HECHO (5.4× → 71s para 3min).

## Reglas heredadas del research

- Reencuadre: **NO usar YOLOv11 (AGPL-3.0)** en código que vaya a producto de pago; para el spike es aceptable, pero marcar la deuda.
- Música: baked-in (fuera del alcance de este spike inicial).
- El objetivo es CALIDAD DE TIMING, no cantidad de features.
