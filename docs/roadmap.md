# Reelfy — Roadmap & contexto

**Creado:** 2026-07-15 (como "Clipfy"). **Renombrado a Reelfy:** 2026-07-15.
**Estado (2026-07-16):** motor completo validado en local (pipeline IA + Reelfy Studio, editor con preview en tiempo real) · empaquetado como app nativa de macOS (`/Applications/Reelfy.app`, `desktop/`) · siguiente: identidad de marca + landing (`reelfy.mixiuh.online`) · multiplataforma en el horizonte.
**Origen:** proyecto para avanzar el portafolio de Mixiuh mientras se destraban Holdyfy/Queryfy en tiendas.

## La idea

Editor de video corto **automático de un solo paso** con IA: "subir video largo → la IA encuentra momentos, corta, subtitula (ES, timing preciso), reencuadra y musicaliza → aprobar/ajustar → exportar listo para publicar". Para SMBs/negocios y personas **sin skills**, en **LATAM/español**.

## Cambio de nombre: Clipfy → Reelfy (2026-07-15)

"Clipfy" chocaba con **`clipfly.ai`** (cuasi-homófono, misma categoría) y **`clipfy.app`** (producto vivo, mismo nombre y TLD, © 2026). La USPTO trata los cuasi-homófonos en categorías competidoras como la causa más común de rechazo → renombrado. **Reelfy** mantiene la familia `-fy` de Mixiuh (Holdyfy, Queryfy), significa reels/shorts sin ambigüedad y se despega de "clip". Va como **subdominio `reelfy.mixiuh.online`** (no requiere dominio propio).

## Veredicto de la investigación (2 rondas, ~214 agentes, verificación adversarial)

### Ronda 1 — Go/No-Go general → **NO-GO como estaba / GO condicionado a nicho afilado**
- Categoría saturada y financiada (OpusClip ~$20M ingresos, $215M valuación, $50M levantados; Submagic).
- Incumbentes ya venden "sube y listo": análisis multimodal, auto-reencuadre 9:16/1:1/16:9, subtítulos ES, tier gratis → **automatización = commodity**.
- Stack local viable (whisper.cpp Metal ~10x realtime; ES WER ~4-6%) **pero** el repo de auto-reencuadre arrastra **YOLOv11 AGPL-3.0** (trampa de licencia copyleft para producto de pago → cambiar detector).
- **Música = bloqueador legal más profundo**: Suno/Udio en litigio; MusicGen pesos CC-BY-NC (no comercial). Vía limpia = librería royalty-free pre-licenciada baked-in.
- Mercado LATAM/español real y creciente (~US$6.68B a 2030, ~14% CAGR) pero TAM del nicho no cuantificable fiable.
- Marca "Clipfy" ocupada.

### Ronda 2 — Afilado del wedge single-pass → **trampa como concepto; sobrevive hueco de ejecución**
- La costura "dos herramientas" (OpusClip encuentra → Submagic estiliza) **ya se cierra por ambos lados**: Submagic hace encontrar+estilizar en un flujo; OpusClip lanzó 10+ estilos de captions integrados. **Single-pass no es defendible solo.**
- "Español-nativo" tampoco es único: Submagic ya publicita 48 idiomas (ES LATAM y europeo).
- "Local-first en Apple Silicon" **no es moat**: repos open-source ya replican el pipeline exacto, incluso 100% local en Apple Silicon (`AI-Youtube-Shorts-Generator` 4.2k⭐, `reelweave`).
- **El único hueco durable = calidad de ejecución donde la automatización falla**: **timing/sincronía de captions** (board de OpusClip: 50 votos, 6 quejas fusionadas, "unusable", "in progress"). Los repos ligeros no igualan el estilizado de calidad.

## Decisión (2026-07-15)

**Proceder con Reelfy asumiendo el riesgo**, con foco quirúrgico en el wedge de ejecución:
- Ganar **de forma medible** en **captions ES con timing/sincronía + estilizado listo-para-publicar**.
- Paridad (no perder) en highlights/cortes/reencuadre.
- Música **solo baked-in** (Pixabay-in-work / ES Partner API); nunca librería descargable.
- Local en M2 Max como implementación (costo ~$0), no como argumento de venta.
- Moat medio y que **decae en meses** → moverse rápido, validar calidad antes de construir features.

## Decisiones de alcance heredadas

- **Usuario meta:** SMBs/negocios sin skills, LATAM/español primero. **Plataforma:** desktop-first en M2 Max.
- **Monetización:** inclinación freemium SaaS (a confirmar). **APIs:** local/abierto primero; nube de pago medida solo en capa inteligente, más adelante. **NO** automatizar suscripciones personales (ToS/ban).

## Stack local/gratis candidato

whisper.cpp (subtítulos ES + timing) · auto-editor (silencios) · PySceneDetect (escenas) · FFmpeg (procesamiento/quemar subs/reencuadre/export/miniaturas) · detector de objetos **con licencia permisiva** para reencuadre (evitar YOLOv11 AGPL) · LLM sobre transcript (highlights, local o Gemini Flash) · música royalty-free baked-in.

## MVP mínimo (a definir en detalle)

Enfoque: **el video vertical final, subtitulado en español con timing impecable, en un solo paso**, sobre un video de prueba real. Lo mínimo para probar/negar el wedge de calidad — no un producto completo.

## Pendientes inmediatos

- [x] Definir alcance del MVP y validarlo — **hecho y superado**: pipeline completo + Reelfy Studio (editor interactivo, preview en tiempo real) + `.app` de macOS.
- [x] **Spike de validación en M2 Max** — hecho con video real (captions ES con alineación forzada, tracking con resorte crítico, zero-cascade audio).
- [x] Detector de reencuadre permisivo — YuNet (Apache-2.0).
- [ ] Logo Reelfy (familia de monogramas Mixiuh) → Kevin lo genera con IA externa.
- [ ] **Landing page** en `reelfy.mixiuh.online` (siguiente bloque tras el logo).
- [ ] Confirmar términos/costos de música para distribución (hoy: pistas propias generadas royalty-free baked-in).
- [ ] Head-to-head medible vs OpusClip/Submagic con el mismo video (evidencia para la landing).
- [ ] (Opcional) renombrar repo/carpeta local `clipfy` → `reelfy`.

## Multiplataforma (roadmap técnico)

Reelfy debe correr **más allá de macOS** (decisión 2026-07-16). Estado real del stack por pieza:

- **Ya portable** (Python/JS puro): FastAPI + Studio (WebView), YuNet/OpenCV, ctc-forced-aligner (torch CPU), librosa, Pillow, ollama (existe en Win/Linux), whisper.cpp (compila en Win/Linux con CUDA/Vulkan en vez de Metal).
- **Atado a macOS hoy**: encode/decode **VideoToolbox** (→ NVENC/QSV/VAAPI o x264 según plataforma), rutas absolutas a `ffmpeg-full`/`ffprobe` de Homebrew (→ resolver binario por plataforma o embeber build estático de ffmpeg CON libass), carcasa **Swift/WKWebView** (→ Tauri v2 como shell multiplataforma manteniendo el mismo Studio HTML, o Electron como fallback).
- **Plan por fases**: (1) abstraer capa de binarios/encoders en `pipeline.py` (tabla por plataforma, detección en runtime); (2) empaquetado del motor con venv embebido o PyInstaller por OS; (3) shell Tauri v2 (Win/Linux/macOS) reutilizando `static/index.html` tal cual; (4) CI de builds por OS. La `.app` Swift actual queda como shell nativo premium de macOS mientras tanto.
