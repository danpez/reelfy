# Ensambla el motor de Reelfy dentro de src-tauri/engine/ para Windows (x64).
# Requiere que whisper.cpp ya esté clonado+compilado en spike/whisper.cpp/build
# (lo hace el workflow de CI, generador MSVC -> build/bin/Release). El modelo
# grande de whisper se descarga en runtime.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)      # -> reelfy-desktop/
$ROOT   = (Resolve-Path "..").Path
$SPIKE  = Join-Path $ROOT "spike"
$ENGINE = "src-tauri/engine"
$CACHE  = ".engine-cache"
$PYURL  = "https://github.com/astral-sh/python-build-standalone/releases/download/20250409/cpython-3.12.10+20250409-x86_64-pc-windows-msvc-install_only.tar.gz"
$FFURL  = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"

New-Item -ItemType Directory -Force $CACHE | Out-Null

Write-Host "==> [1/5] codigo + assets"
if (Test-Path $ENGINE) { Remove-Item -Recurse -Force $ENGINE }
foreach ($d in @("scripts","app","assets","models","bin","whisper.cpp/build/bin")) {
  New-Item -ItemType Directory -Force (Join-Path $ENGINE $d) | Out-Null
}
Copy-Item -Recurse -Force "$SPIKE/scripts/*" "$ENGINE/scripts/"
Copy-Item -Recurse -Force "$SPIKE/app/*"     "$ENGINE/app/"
Get-ChildItem -Recurse "$ENGINE" -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Copy-Item -Recurse -Force "$SPIKE/assets/music" "$ENGINE/assets/"
Copy-Item -Force "$SPIKE/models/face_detection_yunet_2023mar.onnx" "$ENGINE/models/"
Copy-Item -Force "$SPIKE/models/rnnoise.rnnn" "$ENGINE/models/"

Write-Host "==> [2/5] whisper-cli.exe + DLLs"
$wbin = "$SPIKE/whisper.cpp/build/bin/Release"
if (-not (Test-Path "$wbin/whisper-cli.exe")) { $wbin = "$SPIKE/whisper.cpp/build/bin" }
Copy-Item -Force "$wbin/whisper-cli.exe" "$ENGINE/whisper.cpp/build/bin/"
Get-ChildItem "$wbin/*.dll" -ErrorAction SilentlyContinue | Copy-Item -Destination "$ENGINE/whisper.cpp/build/bin/" -Force

Write-Host "==> [3/5] ffmpeg/ffprobe (win64 con libass)"
if (-not (Test-Path "$CACHE/ff/ffmpeg.exe")) {
  Invoke-WebRequest -Uri $FFURL -OutFile "$CACHE/ff.zip"
  Expand-Archive -Path "$CACHE/ff.zip" -DestinationPath "$CACHE/ffx" -Force
  New-Item -ItemType Directory -Force "$CACHE/ff" | Out-Null
  Get-ChildItem -Recurse "$CACHE/ffx" -Include ffmpeg.exe,ffprobe.exe |
    ForEach-Object { Copy-Item $_.FullName "$CACHE/ff/" -Force }
}
Copy-Item -Force "$CACHE/ff/ffmpeg.exe","$CACHE/ff/ffprobe.exe" "$ENGINE/bin/"

Write-Host "==> [4/5] ollama embebido (no-fatal)"
try {
  $rel = Invoke-RestMethod "https://api.github.com/repos/ollama/ollama/releases/latest"
  $asset = $rel.assets | Where-Object { $_.name -eq "ollama-windows-amd64.zip" } | Select-Object -First 1
  if ($asset) {
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile "$CACHE/ollama.zip"
    Expand-Archive -Path "$CACHE/ollama.zip" -DestinationPath "$ENGINE/bin/ollama-runtime" -Force
    # Podar runners/libs de GPU (CUDA/ROCm): pesan >1GB y usamos CPU (llama-server).
    # Sin esto, el motor supera 2GB y NSIS no puede empaquetarlo.
    Get-ChildItem -Recurse "$ENGINE/bin/ollama-runtime" -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match 'cuda|rocm|cublas|cudnn|rocblas|hipblas|amdhip|_v11|_v12' } |
      Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "    ollama $($rel.tag_name) embebido (runners GPU podados)"
  } else { Write-Host "    (asset de ollama no encontrado; sin LLM embebido)" }
} catch { Write-Host "    ollama no se pudo obtener; el build continua sin LLM embebido" }

Write-Host "==> [5/5] python-standalone + dependencias"
if (-not (Test-Path "$CACHE/python.tar.gz")) {
  Invoke-WebRequest -Uri $PYURL -OutFile "$CACHE/python.tar.gz"
}
tar -xzf "$CACHE/python.tar.gz" -C "$ENGINE"          # -> engine/python (python.exe en la raiz)
& "$ENGINE/python/python.exe" -m pip install -q --no-warn-script-location `
  --extra-index-url https://download.pytorch.org/whl/cpu -r "$SPIKE/requirements.txt"
Get-ChildItem -Recurse "$ENGINE/python" -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
# datos de prueba de librerias cientificas: peso muerto (ayuda al limite de NSIS)
Get-ChildItem -Recurse "$ENGINE/python/Lib/site-packages" -Directory -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -in @('tests','test') } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

$sz = (Get-ChildItem -Recurse $ENGINE -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("OK -> $ENGINE ({0:N0} MB)" -f $sz)
Get-ChildItem $ENGINE -Directory | ForEach-Object {
  $s = (Get-ChildItem -Recurse $_.FullName -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1MB
  Write-Host ("   {0}: {1:N0} MB" -f $_.Name, $s)
}
