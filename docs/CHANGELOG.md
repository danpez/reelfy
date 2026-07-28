# Reelfy — Changelog

Formato: cada versión = un tag `reelfy-v*` que dispara el build de los 3 instaladores.

## v0.4.0 — 2026-07-28

**Editor de video profesional + Reelfy Studio renovado.** El montaje en línea de tiempo
pasa a ser el primer paso; la IA se aplica sobre tu composición.

### Editor de línea de tiempo (nuevo NLE por capas)
- **Capas de video**: los clips no se enciman; arrastrar vertical mueve/crea capas (cutaway full-frame).
- **Magnetismo con prioridad y resistencia** (histéresis 9/16 px): bordes de clip > playhead > imágenes.
- Recorte por bordes responsivo, división, arrastre libre + snapping, zoom (mínimo = todo el timeline visible).
- Fades (audio+video), transiciones crossfade, volumen/mute por clip.
- Deshacer/rehacer, atajos de teclado, scrubbing en la regla, divisores redimensionables.
- Backend de composición `compose.py`: concat/xfade, capas full-frame, PiP/stickers.

### Reelfy Studio (preview + IA)
- **Previsualizador canvas en tiempo real**: cualquier ajuste se ve al instante (sin re-render).
- Personalización completa de captions (estilos, keyword highlight, karaoke/box, emojis) + presets.
- Biblioteca de música con búsqueda/preview; B-roll con UI de aprobación; iconos de ayuda.

### Correcciones
- Botón "Continuar al editor" se habilita con video adjuntado (era un error de JS que detenía todo el script: referencia a `#asmMain` inexistente tras el sistema de capas).
- Se respetan en el editor los **nombres propios** y el **nº de shorts** de la pantalla de subida (antes se ignoraban / hardcodeados a 2).
- `Play` y recorte por bordes arreglados; fade in/out ahora visible; división sin encimar.

---

## v0.3.0 — 2026-07-22
- Reelfy Studio con editor y preview en tiempo real; feedback inicial de 8 puntos.

## v0.2.2 — 2026-07
- Fix del DMG de marca en CI (dmgbuild en vez de AppleScript de Finder, que fallaba headless).

## v0.2.x — 2026-07
- Multiplataforma con Tauri 2: Windows (NSIS) y Linux (.deb) además de macOS.
- Release automático al taguear `reelfy-v*` con firma/notarización de macOS.
- Alineador MMS_FA exportado a ONNX int8 (sin torch): más ligero, rápido e igual en Windows.

## v0.1.0 — 2026-07-15
- Motor IA completo validado en local; app nativa de macOS. Renombrado de Clipfy → Reelfy.
