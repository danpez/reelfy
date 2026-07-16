# Clipfy 🎬

**Editor de video 100% automático potenciado por IA.** Un producto de [Mixiuh](https://mixiuh.online).

> Cargas uno o varios videos → la IA hace **todo** → ves el resultado, apruebas o ajustas → exportas listo para cada plataforma.

Clipfy **no es un editor más**: es **automatización total con cero curva de aprendizaje**. Para gente que no tiene tiempo de editar o que no sabe usar herramientas de edición.

---

## El problema

Editar video es lento y tiene curva de aprendizaje. Las herramientas actuales (CapCut, Opus Clip, Submagic, etc.) o **cobran la capa IA** tras un trial/N usos, o **siguen exigiendo edición manual**. Mucha gente —sobre todo SMBs y negocios— solo quiere **contenido listo**, no aprender a editar.

## La propuesta

Un flujo de **una sola decisión**: subir → *(la IA analiza, corta, subtitula, musicaliza, encuadra, genera miniaturas)* → **aprobar o ajustar** → exportar.

### Qué hace la IA (visión)
- Análisis del contenido del video
- Subtítulos automáticos (español nativo)
- Cortes automáticos (silencios, muletillas, escenas)
- Transiciones y ritmo
- Música sin copyright
- Reencuadre a vertical/cuadrado/horizontal (9:16, 1:1, 16:9)
- Miniaturas
- Export listo por plataforma

## Posicionamiento

| | |
|---|---|
| **Usuario meta (fase 1)** | SMBs / negocios / personas **sin skills** de edición |
| **Mercado inicial** | **LATAM / español** (se apalanca en la audiencia de Holdyfy) |
| **Plataforma (MVP)** | **Desktop primero** (macOS Apple Silicon — se prueba en MacBook Pro M2 Max) |
| **Diferenciación** | Español/LATAM + verticalización a SMBs + **modo 100% automático, cero-UI** |
| **Monetización (hipótesis)** | Freemium SaaS — a confirmar con investigación de mercado |

> Categoría muy saturada y financiada. **Ir de frente contra CapCut = perder.** La apuesta es un **nicho afilado**, no competir en features.

## Filosofía técnica

**Fase inicial = stack local / open-source (gratis), corriendo on-device en Apple Silicon** (costo marginal ~$0). APIs de nube de pago (p.ej. Gemini Flash) **solo** para la capa "inteligente" y **más adelante**.

⚠️ **NO** se automatizarán suscripciones personales (ChatGPT Plus / Gemini Advanced / Claude Pro): viola sus ToS, es inestable y arriesga baneos. Para usar esos modelos en producto → **API de pago medida por uso**.

### Stack candidato (local-first)

| Función | Herramienta | Costo |
|---|---|---|
| Subtítulos / transcripción | **Whisper** (whisper.cpp / faster-whisper) | Local, gratis |
| Cortes de silencios / muletillas | `auto-editor` | Local, gratis |
| Cortes por escena | **PySceneDetect** | Local, gratis |
| Procesamiento de video (cortes, transiciones, quemar subs, reencuadre, export, miniaturas) | **FFmpeg** | Local, gratis |
| Selección de highlights | LLM sobre el transcript (local o Gemini Flash barato) | ~$0 / centavos |
| **Música sin copyright** ⚠️ | Librería royalty-free (Pixabay/Uppbeat) · MusicGen local (pesado) · Suno/Udio (pago, licencia restringida) | **Eslabón débil** |

## Competencia (a profundizar)

CapCut · Opus Clip / Klap · Submagic / Captions · Descript · Veed.io / Kapwing · Pictory · InVideo AI · Gling · vidyo.ai. Por arriba: Runway, Adobe Firefly, Sora/Veo.

## Estado

🔬 **Investigación de viabilidad/mercado en curso** (competencia real y precios, hueco "sube y listo", viabilidad/costo del stack local, licencias, tamaño de mercado LATAM, modelo de negocio, alcance de MVP, veredicto go/no-go).

Ver [`docs/roadmap.md`](docs/roadmap.md).

---

_Nota de marca: dominios `clipfy.com/.app/.online/.ai/.co` parecen ocupados; `clipfy.io` se ve libre (verificar). Puede haber otro producto llamado "Clipfy" — validar marca antes de lanzar._
