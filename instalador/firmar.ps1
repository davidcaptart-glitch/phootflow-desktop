# Firma de código de los ejecutables (la usa el build de GitHub). Sin certificado configurado no hace nada,
# así el build sigue funcionando; con él, Windows (SmartScreen y Smart App Control) reconoce al editor.
#
# Secretos del repo (Settings > Secrets and variables > Actions), los crea David, nunca en el código:
#   SIGN_PFX_BASE64   el certificado .pfx en base64  ([Convert]::ToBase64String([IO.File]::ReadAllBytes("cert.pfx")))
#   SIGN_PFX_PASSWORD su contraseña
# Si el certificado es de los que viven en la nube (Azure Trusted Signing, Certum SimplySign…), este script se
# cambia por el paso de firma de ese proveedor; el resto del build no cambia.
param([Parameter(Mandatory = $true)][string[]]$Files)
$ErrorActionPreference = "Stop"

if (-not $env:SIGN_PFX_BASE64) {
    Write-Host "Sin certificado de firma: los archivos quedan sin firmar."
    exit 0
}
$signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
    Sort-Object FullName | Select-Object -Last 1 -ExpandProperty FullName
if (-not $signtool) { throw "No se encuentra signtool.exe (Windows SDK)." }
$pfx = Join-Path $env:RUNNER_TEMP "firma.pfx"
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:SIGN_PFX_BASE64))
try {
    foreach ($f in $Files) {
        & $signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f $pfx /p $env:SIGN_PFX_PASSWORD /d PhootFlow $f
        if ($LASTEXITCODE -ne 0) { throw "No se pudo firmar $f" }
        & $signtool verify /pa $f
        if ($LASTEXITCODE -ne 0) { throw "La firma de $f no es válida" }
    }
} finally {
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}
