# Instala PhootFlow (versión con Python) y crea accesos directos. Se actualiza sola desde las Releases.
# Uso:  powershell -ExecutionPolicy Bypass -File instalar.ps1
# Es la vía recomendada en PCs con Smart App Control, que bloquea el .exe sin firmar.
$ErrorActionPreference = "Stop"
$repo = "davidcaptart-glitch/phootflow-desktop"
$dest = Join-Path $env:LOCALAPPDATA "Programs\PhootFlow"

$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { throw "Hace falta Python 3.11 o superior (python.org o Microsoft Store)." }

Write-Host "Descargando la última versión de PhootFlow…"
$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" -Headers @{ "User-Agent" = "PhootFlow-instalar" }
$zipUrl = ($rel.assets | Where-Object name -eq "PhootFlow-src.zip").browser_download_url
$shaUrl = ($rel.assets | Where-Object name -eq "PhootFlow-src.zip.sha256").browser_download_url
$tmp = Join-Path $env:TEMP "PhootFlow-src.zip"
Invoke-WebRequest $zipUrl -OutFile $tmp -UseBasicParsing
$shaFile = Join-Path $env:TEMP "PhootFlow-src.zip.sha256"
Invoke-WebRequest $shaUrl -OutFile $shaFile -UseBasicParsing
$expected = ([IO.File]::ReadAllText($shaFile)).Trim().ToLower()
if ((Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower() -ne $expected) { throw "La descarga no coincide con su suma de verificación." }

New-Item -ItemType Directory -Force $dest | Out-Null
$x = Join-Path $env:TEMP "PhootFlow-src"
if (Test-Path $x) { Remove-Item -Recurse -Force $x }
Expand-Archive $tmp -DestinationPath $x
Copy-Item -Recurse -Force (Join-Path $x "PhootFlow\*") $dest

Write-Host "Instalando dependencias…"
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $dest "requirements.txt")

$pyw = Join-Path (Split-Path $py) "pythonw.exe"
if (-not (Test-Path $pyw)) { $pyw = $py }
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Desktop"), (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"))) {
    $lnk = $sh.CreateShortcut((Join-Path $dir "PhootFlow.lnk"))
    $lnk.TargetPath = $pyw
    $lnk.Arguments = "`"$(Join-Path $dest 'app.py')`""
    $lnk.WorkingDirectory = $dest
    $lnk.IconLocation = (Join-Path $dest "assets\icon.ico")
    $lnk.Save()
}
Write-Host "PhootFlow $($rel.tag_name) instalado en $dest. Ábrelo desde el escritorio."
