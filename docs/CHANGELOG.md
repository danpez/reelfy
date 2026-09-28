# Reelfy — Changelog

Formato: cada versión = un tag `reelfy-v*` que dispara el build de los 3 instaladores.

## v0.5.1 — 2026-09-28

**Videos largos y cortes más rigurosos.**

### Correcciones
- **Exportar videos largos ya no falla**: con más de ~100 cortes (p. ej. 19 min con 211 cortes) ffmpeg
  no podía interpretar la selección de tramos ("Cannot allocate memory"). Ahora se encadenan varios
  filtros cortos: exacto al cuadro, para cualquier número de cortes (también en shorts y el modo clásico).
- **Director en videos largos**: el guion se analiza en bloques en paralelo y el análisis visual tiene un
  presupuesto de tiempo que crece con la duración; un video de 19 min recibe dirección de la IA en
  ~3.5 min (antes 8 min y terminaba en modo básico).
- El video base decodifica H.264 por software (más rápido que VideoToolbox en 4K H.264).
- El render usa tantos procesos como permite la RAM (una Mac de 8 GB ya no hace swap).

### Cortes de silencio
- Las pausas entre palabras de 0.4 s o más se recortan aunque haya ruido de fondo (antes solo con
  silencio "digital"), dejando un respiro corto; la voz nunca se corta.

---

## v0.5.0 — 2026-09-28

**Motion Engine: edición profesional automática, 100% local.** De clips crudos a un video
editado como un editor pro de TikTok/Reels, y todo se puede ajustar en el Studio.

### Director IA (Qwen3.5-4B, local, con visión)
- Entiende el guion y las tomas (lee letreros) y decide títulos cinéticos sincronizados a la voz,
  garabatos e íconos que se dibujan, tarjetas (precio con contador, ubicación, horarios),
  escenas a pantalla completa, blanco y negro dramático, transiciones (whip, zoom blur, fuga de
  luz, flash, papel rasgado), cortes de silencios y color igualado entre tomas.
- Estilos (dinámico, elegante, minimal, energético) e intensidad; regenerar, bloquear y re-dirigir.
- **Simetría**: todo lo que entra a pantalla deja el mismo espacio a izquierda y derecha
  (centrado o en par espejo).
- Look de color natural por defecto; los efectos que dependen del recorte de la persona
  (fondo reemplazado, sujeto a color, contorno) ya no se agregan solos.

### Audio
- Efectos de sonido propios (sintetizados, sin licencias) y música instrumental generada en tu
  equipo con ACE-Step (descarga opcional de ~3.7 GB en segundo plano), cortada al video.
- Mezcla a -14 LUFS con la voz siempre al frente.

### Perfil
- Pegas "soy la Dra. X, odontóloga, mi instagram es @x y mi página es ..." y Reelfy integra
  rótulo con nombre, botón Seguir con @usuario, "Agenda en <página>" y tarjeta final.
- Obtiene solo la foto de cada enlace (Instagram: foto de perfil; otras páginas: su foto y el
  logo del sitio) para el botón Seguir, la pastilla de reservas y las tarjetas.

### Studio
- Pistas de texto, gráficos, escenas, efectos, transiciones, SFX y música; inspector de
  parámetros; cuadro exacto al pausar y vista previa real con audio; imán al centro al arrastrar.

### Correcciones
- Clips de iPhone rotados y en HDR se montan bien; la música ya no baja el volumen de la voz.
- La app ya no escribe `.pyc` dentro del bundle (rompía la firma de macOS).
- Al salir con Cmd+Q el motor y su ollama se apagan; un ollama huérfano de una sesión anterior se
  termina al arrancar, y el motor se apaga solo si la app muere.
- Primer arranque con los tamaños reales (~5.5 GB + música opcional ~3.7 GB).

---

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
