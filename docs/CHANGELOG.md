# Reelfy — Changelog

Formato: cada versión = un tag `reelfy-v*` que dispara el build de los 3 instaladores.

## v0.6.0 — 2026-10-03

**Cada ajuste dice qué hace y lo muestra con tu propio video.**

### Estilo (nueva pestaña con todo el look del video)
- Subtítulos: galería de estilos con ejemplo en vivo sobre tu cuadro y tus palabras. «Pastilla de
  color» queda a un clic, con su color justo debajo; la vista previa desliza la pastilla con el mismo
  resorte y sombra que el export.
- Cada color muestra dónde se usa («ya está en…»), y «Color de tu marca» se ve en vivo en títulos,
  tarjetas y botones. La pastilla puede seguir tu marca o tener su propio color.
- Color del video: los seis estilos con antes/después real de tu cuadro (hecho por el motor), línea
  para comparar y ajustes finos con ejemplos. «Recomendado» en Natural (el estilo por defecto no cambia).
- Edición con IA: cuatro tarjetas de estilo con vista previa, «Qué cambia» y «Cantidad de efectos» con
  tu conteo real («tu video lleva 2 títulos, 3 gráficos…»).
- Música: «Escuchar» en cada fuente y ambiente, volumen con muestra de 5 s, y la descripción que
  escribes se respeta al cambiar de estilo.
- Quién sale y tu logo: tarjeta del perfil, «Dónde aparece» y logo con ejemplo de la esquina.
- Básico y Avanzado: Básico usa opciones en palabras (Chico · Mediano · Grande); los números y
  unidades quedan en Avanzado, siempre en español.

### Elementos del video
- Cada ajuste del panel es una fila con nombre, para qué sirve y ejemplo.
- Tipos de título, gráficos, escenas, efectos y transiciones en galerías animadas con tu cuadro; un
  clic cambia el tipo. Animaciones, figuras y fondos se animan al pasar el mouse.
- «Agregar» con miniaturas animadas; los sonidos se escuchan antes de agregarlos.

### Otras pantallas
- Inicio, Montar clips y Limpieza explican cada opción con tus números («Normal · quita 4 tomas
  repetidas · queda 2:54») y cada tipo de corte se puede escuchar.
- «¿Cuántos shorts sugerir?» admite «Ninguno».

### Correcciones
- Se quitaron controles que no hacían nada («Caja de fondo», «Posición X», colores que el motor no
  usa); los que no aplican a un estilo se desactivan con la razón.
- En la app de escritorio las vistas previas ya no salen negras.
- La limpieza de disco nunca borra dentro de una carpeta enlazada.

---

## v0.5.5 — 2026-10-01

**Exportar como tú quieras (hasta 4K), lo negativo se ve negativo e imágenes con vida.**

### Exportar
- Nueva ventana de exportación: formato, resolución (720p, 1080p, 1440p, 4K), calidad (Estándar /
  Alta / Máxima), nombre del archivo, carpeta (selector de macOS; se recuerda) e incluir shorts, con el
  tiempo y el peso estimados antes de exportar.
- 4K real: si tus clips originales lo tienen, Reelfy vuelve a montar el video desde ellos a la
  resolución elegida (nunca escala de más; avisa si un clip es de menor resolución).
- Al terminar: ruta, peso y resolución, con "Mostrar en Finder" y "Abrir". Nunca sobrescribe un
  archivo (agrega " (2)").

### Edición con IA
- Las frases negativas (errores, "nadie", "cero", fracasos, dudas) reciben un tratamiento serio: color
  apagado o blanco y negro frío con viñeta, un glitch corto, un título serio, un ícono sobrio y un golpe
  grave; ya no llevan estrellitas ni adornos de celebración. Las frases positivas no cambian.
- El color y el estilo de subtítulos que ajustas ya no se pierden al re-dirigir.
- Una palabra en color oscuro dentro de un título claro lleva un contorno fino para leerse.

### Recursos flotantes
- Las imágenes flotan, se inclinan en 3D con perspectiva, tienen acercamiento con paralaje y un brillo
  de luz que las cruza (con reflejo irisado suave); los videos flotan y se inclinan.

### Correcciones
- El video compuesto queda exacto al cuadro (antes el render podía mostrar el cuadro anterior).

---

## v0.5.4 — 2026-09-30

**Cambios de cámara con aceleración y recursos flotantes desde el guion.**

### Cambios de cámara
- Cuando el montaje alterna dos cámaras o parte una frase entre dos tomas, la imagen se acelera a 2x
  unos 0.3 s alrededor del cambio (la toma que sale o la que entra, del lado donde hay pausa y cuadros
  de sobra) mientras el audio sigue a velocidad normal: un cambio fluido, como una edición a mano.
- Cada cambio de cámara muestra un marcador en el montaje para quitar o poner la aceleración.

### Recursos desde el guion
- Al final de una línea del guion puedes escribir "- Agregar nombre_del_archivo" (o "[Agregar ...]"):
  Reelfy busca ese archivo entre los que subiste y lo muestra mientras se dice esa línea, como una
  tarjeta flotante (esquinas redondeadas, sombra, la persona de fondo desenfocada, entrada con resorte,
  flotación suave; los videos van sin audio y las imágenes con un acercamiento lento), centrada y sin
  tapar los subtítulos. Si el video es más largo que la frase se recorta o se acelera un poco.
- En el montaje: "Recursos del guion" con lo encontrado y lo que falta ("Agregar archivo"); en el
  Studio, la pista Recursos para moverlos, recortarlos o cambiar el archivo.
- El director no pone títulos, tarjetas ni transiciones encima de un recurso.

### Color de acento
- Un solo ajuste "Color de acento" por proyecto (subtítulos, títulos y tarjetas), amarillo por defecto.

---

## v0.5.3 — 2026-09-30

**Armar con IA: de todos los clips de una grabación al video, con o sin guion.**

### Armar con IA (nuevo)
- Seleccionas TODOS los clips de una grabación y (opcional) pegas el guion. Reelfy transcribe cada
  clip al importarlo, los ordena por hora de grabación y elige la mejor toma de cada parte del guion:
  descarta repeticiones, tomas con trabas, groserías, pláticas fuera de cámara y lo que no va en el
  guion; puede alternar dos ángulos de la misma frase o completar una frase con dos tomas.
- Sin guion, lo deduce de las tomas repetidas y te lo muestra ("Guion detectado") para corregirlo y
  rearmar.
- En el montaje, el panel **Tomas** explica por qué eligió cada toma, muestra las otras con
  "Usar esta toma" (con deshacer), las descartadas con su motivo y las partes sin toma.
- Caso real (39 clips, 7 min de material): mismas tomas que la edición a mano en 15 de 17 frases e
  igual de buenas en las 17, 100% del guion, sin repeticiones; el montaje se decide en menos de 1 s.
- Al unir clips: todos suenan al mismo volumen, sin clics en las uniones; cortes secos dentro de una
  frase y transiciones solo entre secciones; el director describe cada cámara (antes el guion le
  ganaba el turno al modelo de visión y ninguna toma se describía).

### Correcciones
- Exports que podían salir mudos: un valor inválido (NaN) al final de la voz procesada apagaba toda
  la mezcla.
- Aviso falso "rango fuera del video" por redondeo en videos compuestos.
- La portada de una plataforma de reservas (paseo.club) ya no se anuncia como "Agenda en ...".
- Las subidas del Studio (b-roll, fotos) no se transcriben de más.

---

## v0.5.2 — 2026-09-29

**Limpieza de tomas: Reelfy quita las repeticiones, correcciones y tomas fallidas.**

### Limpieza de tomas (nuevo)
- Al analizar, Reelfy detecta en la transcripción las frases que repites (se queda la última toma
  completa), los arranques que dejas a medias, las correcciones ("19, no, 16" -> "16"), las palabras
  repetidas de seguido y las groserías entre tomas fallidas. Respeta el énfasis ("muy, muy") y las
  listas. Todo con reglas locales: medio segundo para un video de 19 min.
- Nueva pestaña **Limpieza de tomas** en el Studio (se abre sola tras el análisis): el guion con lo
  quitado tachado y su motivo, "Escuchar cómo queda", "Restaurar" y atajos (Espacio, R, flechas);
  filtros por tipo, mapa de cortes, "Restaurar todo" con deshacer y resumen de la IA con el tema.
- Nivel de limpieza **Suave / Normal / A fondo**; lo que restauras o ajustas a mano se respeta al
  cambiar de nivel.
- El tema del video lo entiende la IA local en una sola consulta (antes tomaba la primera frase).
- Los shorts se eligen después de la limpieza y nunca terminan dentro de una toma quitada.

### Subtítulos
- Ya no se pierden palabras: el alineador fino se descargaba de GitHub y la app instalada no lo tenía
  (hasta 1 de cada 10 palabras desaparecía y otras se quedaban segundos en pantalla). Ahora se usan los
  tiempos por palabra de Whisper (DTW) corregidos con la energía de la voz: error mediano ~45 ms, sin
  descargas extra.

### Proyectos y espacio en disco
- **Eliminar proyecto** (a la Papelera; tus videos originales no se tocan) y **Liberar espacio**
  (borra vistas previas y archivos temporales; el proyecto sigue editable). Tamaño de cada proyecto y
  uso total en la pantalla inicial.
- Reelfy ya no borra proyectos viejos por su cuenta: avisa cuando queda poco espacio.
- Importar el mismo video dos veces ya no lo guarda dos veces.
- El video combinado ya no se recodifica al triple del peso del original (tope 1.2x la fuente, o copia
  directa cuando no hace falta procesarlo).

### Formato
- Un video largo horizontal (más de 3 min) se exporta en 16:9 por defecto (antes salía vertical).
  En ese caso los shorts no se generan todavía (se avisa).

### Correcciones
- El recorte de silencios ya no se come el final de frase dicho en voz baja ("...a mi punto de
  vista."): una palabra transcrita con voz queda protegida; las respiraciones y pausas se siguen
  quitando. En el video de 19 min de prueba: 11 palabras que se cortaban, ahora 0.
- Palabras que el alineador estiraba sobre la pausa siguiente se tachaban en el Studio y salían de
  los subtítulos aunque se oían; ahora se quedan del lado donde hay voz.
- Al cerrar Reelfy ya no quedan procesos corriendo (generador de música, ffmpeg de vistas previas):
  el motor corre en su propio grupo y se barre completo al salir.
- Un clic en un corte o un short de la línea de tiempo solo lo selecciona (antes lo activaba o
  desactivaba).
- El texto "silencios y muletillas" decía algo que no se hacía; ahora dice "silencios".

---

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
