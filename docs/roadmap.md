# Clipfy — Roadmap & contexto

**Creado:** 2026-07-15. **Estado:** investigación de viabilidad/mercado en curso.
**Origen:** proyecto para avanzar el portafolio de Mixiuh mientras se esperan desbloqueos externos (paso a producción de Holdyfy/Queryfy en tiendas + perfil de negocio de Meta restringido que frena Holdyfy IA).

## La idea

Editor de video **totalmente automático** con IA: "cargar uno o varios videos → la IA hace todo (análisis, subtítulos, cortes, transiciones, música sin copyright, miniaturas) → el usuario ve el resultado, aprueba o ajusta → exporta listo para cada plataforma". **No es un editor más; es automatización total con cero curva de aprendizaje.**

## Motivación / tesis

- Kevin no tiene tiempo de editar; y mucha gente **no sabe usar** herramientas de edición.
- Suplir la necesidad de aprender/depender de un editor. UX: **subir → ver → aprobar/ajustar**.
- CapCut & co. **no son gratis** en la capa IA (cobran plantillas/funciones IA tras trial/N usos); el editor manual sí. **El valor está en la capa IA que todos monetizan.**

## Decisiones de alcance (2026-07-15)

- **Usuario meta:** SMBs/negocios sin skills, mercado **LATAM/español** primero (se apalanca en audiencia Holdyfy). Local primero, expandir después.
- **Plataforma:** **Desktop primero**, probando en el **MacBook Pro M2 Max** de Kevin (Apple Silicon → Whisper/FFmpeg/modelos locales corren excelente, costo ~$0 marginal en fase MVP).
- **Monetización:** que la investigación recomiende; inclinación a **freemium SaaS**.
- **APIs:** fase inicial **local/abierto** (gratis). **NO** automatizar suscripciones personales ChatGPT/Gemini/Claude (ToS/ban/ilegal para producto). Para nube usar **API de pago medida** (p.ej. Gemini Flash) solo en la capa "inteligente", más adelante.

## Stack local/gratis candidato

- **Subtítulos:** Whisper (whisper.cpp/faster-whisper), buen español, local.
- **Cortes de silencios/muletillas:** `auto-editor`. **Cortes por escena:** PySceneDetect.
- **Procesamiento** (recortes, transiciones, quemar subs, reencuadre 9:16/1:1/16:9, export, miniaturas): **FFmpeg**.
- **Highlights:** LLM sobre transcript (local o Gemini Flash barato por API).
- **Eslabón débil — música sin copyright:** MusicGen local (pesado, calidad media) o Suno/Udio (pago, restricciones comerciales) → realista arrancar con **librería royalty-free** (Pixabay/Uppbeat).

## Competencia conocida (a profundizar en research)

CapCut (ByteDance), Opus Clip/Klap, Submagic/Captions, Descript, Veed/Kapwing, Pictory, InVideo, Gling, vidyo.ai. Arriba: Runway, Firefly, Sora/Veo. Categoría **muy saturada y financiada** → ganar de frente en features = perder; la apuesta es **nicho afilado** (español/LATAM + SMBs + modo 100% automático cero-UI).

## Investigación en curso (8 ejes)

1. Landscape competitivo detallado (qué es gratis vs paywall, precios 2025-2026, si logran "sube y listo", soporte español, desktop/móvil/web).
2. Hueco de mercado del "sube y listo, cero skills" para no-editores/SMBs en español/LATAM.
3. Viabilidad técnica y costo del stack local en Apple Silicon M2 Max (incl. música — el punto difícil).
4. Licencias / riesgos legales (Whisper, MusicGen, música IA para redistribución comercial).
5. Demanda y tamaño de mercado LATAM/español.
6. Modelo de negocio (freemium vs créditos vs app de pago única).
7. Alcance de MVP realista + diferenciación + qué NO construir.
8. Veredicto **go/no-go** honesto con riesgos y banderas rojas.

## Pendientes inmediatos

- [ ] Terminar la investigación profunda (se ha caído varias veces por reinicios del entorno; relanzar de forma resistente).
- [ ] Validar marca/dominio "Clipfy" (`.io` parece libre; otros TLD ocupados; posible producto homónimo).
- [ ] Con el resultado del research: definir MVP y arrancar prototipo en la MacBook M2 Max (Whisper + FFmpeg + auto-captions + auto-cortes) como spike de validación.
