# Reelfy — app local (MVP)

Interfaz web local que envuelve el pipeline del spike. Para gente sin skills:
arrastra un video → la IA saca los mejores shorts → previsualiza y descarga.
Todo corre en tu Mac, sin nube.

## Correr

```bash
cd spike
./app/run.sh          # abre http://127.0.0.1:8000
```

Abre esa URL en el navegador, arrastra un MP4/MOV, ajusta opciones (glosario de
nombres propios, número de shorts, dinamismo) y dale **Generar shorts**.

## Arquitectura

- **`server.py`** — FastAPI. Envuelve `scripts/pipeline.py` como subproceso (con
  `-u` para streamear el progreso), parsea sus marcadores de avance y expone:
  - `GET /` la interfaz · `POST /process` inicia un job · `GET /status/{id}` progreso
  - `GET /video/{archivo}` sirve los resultados para preview/descarga
- **`static/index.html`** — interfaz de una sola página (HTML/CSS/JS embebidos), marca Reelfy.
- Uploads → `spike/input/<job_id>.<ext>` · resultados → `spike/output/<job_id>_reelfy.mp4` + `_short*.mp4`.

El estado de los jobs vive en memoria (single-user local). El motor no se tocó:
la UI sólo lo orquesta.

## Notas

- Si el video es puro relleno/intro sin un momento destacable, la IA puede no
  devolver shorts; en ese caso se entrega el **video completo** ya procesado
  (subtítulos + reencuadre + dinamismo).
- Siguiente paso posible: empaquetar como app de escritorio (.app) con Tauri/PyInstaller
  para que se abra con doble clic (sin terminal).
