# Reelfy — Arquitectura y stack (estado actual)

**Última actualización:** 2026-07-28 · **Versión:** v0.4.0

Reelfy es un **editor de video corto con IA para desktop** (Win/Mac/Linux) que corre
**100% local y offline**. Convierte un video largo en **shorts verticales listos para
publicar**: subtítulos ES sincronizados palabra por palabra, cortes de silencios,
reencuadre que sigue tu cara, audio de estudio, resaltado de keywords + emojis, y
opcionalmente B-roll. Con un **editor de línea de tiempo profesional** como primer paso.

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
| LLM embebido | **ollama** (binario + runners) | Sirve el modelo de lenguaje local (fuera del `~/.ollama` del sistema) |

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

### 2.3 LLM local — `qwen2.5:7b` sobre `ollama` embebido
El cerebro de decisiones (env `REELFY_LLM_MODEL`, default `qwen2.5:7b`; endpoint
`REELFY_OLLAMA_URL`). Tres usos, **todos degradan con gracia a heurística local** si el
LLM no está disponible:
- **Highlights** (`scripts/highlights.py`): elige los mejores fragmentos → shorts (el "nº de shorts" que pides).
- **Resaltado inteligente** (`scripts/captionsmart.py`): por frase, qué keywords resaltar + qué emojis poner (con densidad configurable).
- **Traducción** (`scripts/translate.py`): subtítulos ES→EN manteniendo el timing, en chunks para no desbordar el contexto.

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
motor/modelos), `/tracks` (catálogo de música), `/brand/logo`, `/settings`.

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
- **Primer arranque**: la app descarga los modelos grandes (~4 GB: whisper turbo + LLM)
  una sola vez a un directorio de datos escribible.

### Notas por plataforma
- **Windows**: NSIS, 64-bit. SmartScreen puede advertir (instalador aún sin firma
  digital) → "Más información" → "Ejecutar de todas formas". Se podan runners GPU de
  ollama (CUDA/ROCm) y torch/torchaudio (NSIS no soporta >2 GB).
- **macOS 13+ / Apple Silicon**: firmado + notarizado (abre sin advertencias). DMG de
  marca vía dmgbuild.
- **Linux**: Ubuntu 22.04+/Debian, `sudo apt install ./Reelfy.deb`.
