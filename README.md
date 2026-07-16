# Reelfy 🎬

**Editor de video corto automático potenciado por IA.** Un producto de [Mixiuh](https://mixiuh.online) · `reelfy.mixiuh.online`

> Cargas un video largo → la IA encuentra los momentos, corta, subtitula (español nativo, **timing preciso**), reencuadra y musicaliza → ves el resultado, apruebas o ajustas → exportas listo para publicar. **En un solo paso.**

Reelfy apuesta por **automatización de una sola decisión**, para SMBs/negocios y personas **sin skills** de edición en **LATAM/español**. No compite en cantidad de features; compite en que el resultado quede **realmente listo para publicar** sin saltar entre dos herramientas.

> _Renombrado desde "Clipfy" (jul 2026): el nombre chocaba con `clipfly.ai` y `clipfy.app`, ambos vivos en la misma categoría → riesgo de confusión de marca. Ver `docs/roadmap.md`._

---

## Postura honesta (leer antes de invertir)

Dos rondas de investigación de mercado (go/no-go + afilado del wedge) dejaron un veredicto claro y **lo asumimos con los ojos abiertos**:

- La categoría (clipping de video corto con IA) está **saturada y muy financiada**. OpusClip: ~$20M ingresos, $215M valuación, $50M levantados (SoftBank). Submagic converge hacia lo mismo.
- **La automatización básica ya es commodity**: "sube y listo", análisis multimodal, auto-reencuadre 9:16/1:1/16:9 y subtítulos en español ya los venden los incumbentes, con tier gratis.
- El **stack local open-source** (Whisper + FFmpeg + LLM) que usaremos **ya existe replicado gratis en GitHub** (incl. modo 100% local en Apple Silicon). "Local-first" **no es un moat**; es un detalle de implementación (costo marginal ~$0).
- El "single-pass" como concepto **no es defendible solo**: los incumbentes ya convergen a hacer encontrar+estilizar en un flujo.

**Entonces, ¿por qué seguimos?** Porque sobrevive **un hueco angosto de EJECUCIÓN**, y ahí está toda la apuesta.

## El único wedge real: calidad de ejecución donde la automatización aún falla

La evidencia señala **un fallo concreto, documentado y con quejas reales**: **timing/sincronía de captions**. El propio board de OpusClip tiene una petición con **50 votos** y 6 quejas fusionadas (captions fuera de sync, *"unusable"*), marcada "in progress". Los repos ligeros open-source tampoco igualan el estilizado de calidad.

**La tesis de Reelfy:** ser mejor —de forma medible— en **captions en español con timing/sincronía perfectos y estilizado listo-para-publicar**, no en tener más botones. El moat es **profundidad de ejecución + UX + precio**, no la categoría. Es un moat **medio y que decae en meses** → hay que moverse rápido y validar la calidad antes que construir features.

## Qué hace (visión de producto)

Flujo de una sola decisión: subir → *(IA: análisis, highlights, cortes de silencios/muletillas/escenas, subtítulos ES con timing preciso, reencuadre, música baked-in)* → **aprobar o ajustar** → exportar por plataforma.

| Capa | Rol en el wedge |
|---|---|
| Subtítulos ES + **timing/sync** | ⭐ **el diferenciador** — calidad medible superior |
| Estilizado de captions (animados, keyword highlight, plantillas) | ⭐ parte del "listo para publicar" |
| Highlights / cortes / reencuadre | paridad con incumbentes (no perder aquí) |
| Música | **solo baked-in** (ver licencias) — no es diferenciador |

## Música: solo *baked-in*, nunca librería descargable

Regla dura de licencias (investigada): la música va **incrustada en el video renderizado**, **jamás** expuesta como librería seleccionable/descargable dentro de la app.

- ✅ **Pixabay Music** — uso comercial permitido cuando va como parte de una obra mayor (video renderizado). Prohibido exponerla standalone.
- ✅ **Epidemic Sound Partner API** — opción licenciada (el partner tiene el acceso; el usuario no necesita cuenta). Ojo: licencia auto-otorgada es uso personal; monetización comercial requiere plan pago del usuario.
- ❌ **Uppbeat** — su acuerdo estándar prohíbe redistribuir/incrustar en otra plataforma.
- ❌ **Suno/Udio** — en litigio; propiedad legalmente blanda; no autoriza redistribución SaaS.
- ❌ **MusicGen** — pesos CC-BY-NC (no comercial). Descartado para producto de pago.

## Filosofía técnica

Fase inicial = **stack local/open-source corriendo on-device en Apple Silicon** (MacBook Pro M2 Max, costo marginal ~$0). APIs de nube de pago (p.ej. Gemini Flash) **solo** para la capa "inteligente" y **más adelante**.

⚠️ **NO** automatizar suscripciones personales (ChatGPT Plus / Gemini Advanced / Claude Pro): viola ToS, inestable, riesgo de baneo. Para modelos de nube en producto → **API de pago medida por uso**.

### Stack candidato (local-first)

| Función | Herramienta | Costo | Nota |
|---|---|---|---|
| Subtítulos / transcripción ES | **whisper.cpp** (Metal ~10x realtime; ES WER ~4-6%) · MIT | Local, gratis | base del wedge |
| Cortes de silencios / muletillas | `auto-editor` | Local, gratis | |
| Cortes por escena | **PySceneDetect** | Local, gratis | |
| Procesamiento (cortes, transiciones, quemar subs, reencuadre, export, miniaturas) | **FFmpeg** | Local, gratis | |
| Reencuadre con seguimiento de sujeto | detector de objetos ⚠️ | Local, gratis | **evitar YOLOv11 (AGPL-3.0)** en producto de pago → usar detector con licencia permisiva |
| Selección de highlights | LLM sobre el transcript (local o Gemini Flash barato) | ~$0 / centavos | |
| Música | Pixabay-in-work / ES Partner API | ver arriba | baked-in |

## Qué NO construir

Un clip-finder novedoso · una librería de música descargable · "local-first" como argumento de venta · competir en cobertura de idiomas · features de paridad antes de ganar en timing/calidad de captions.

## Estado

🔬 Investigación cerrada (go/no-go + afilado del wedge). **Decisión: proceder con Reelfy asumiendo el riesgo**, enfocando el MVP en el wedge de ejecución.

Siguiente: definir MVP mínimo y **spike de validación en la M2 Max** — probar si podemos generar captions en español con timing/sincronía **medible­mente mejores** que OpusClip/Submagic. Si el timing sale superior, hay producto; si no, se reevalúa con datos.

Ver [`docs/roadmap.md`](docs/roadmap.md).
