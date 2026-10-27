# Publica o .exe self-contained single-file e gera o instalador NSIS (se o makensis estiver instalado).
# Uso: .\publish.ps1 [-Version 2.1.0]   (sem -Version usa a última tag vX.Y.Z do git, ou 0.0.0-dev)
param([string]$Version)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $Version) {
    $tag = git -C $root describe --tags --abbrev=0 --match "v*" 2>$null
    $Version = if ($tag) { $tag.TrimStart("v") } else { "0.0.0-dev" }
}
$out = Join-Path $root "publish"
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue

dotnet publish "$root\PolNotifier\PolNotifier.csproj" -c Release -r win-x64 --self-contained `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:DebugType=none `
    -p:Version=$Version -o $out
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Host "OK: $out\PolNotifier.exe ($Version)"

$makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $makensis -and (Test-Path "${env:ProgramFiles(x86)}\NSIS\makensis.exe")) { $makensis = "${env:ProgramFiles(x86)}\NSIS\makensis.exe" }
if (-not $makensis) { Write-Warning "makensis não encontrado: instalador não gerado (instale o NSIS: winget install NSIS.NSIS)"; exit 0 }
# O NSIS só aceita versão numérica X.Y.Z.W no VIProductVersion
$numeric = ($Version -split "[-+]")[0]
& $makensis /V2 "/DVERSION=$Version" "/DNUMVERSION=$numeric.0" "/DSRCDIR=$out" "/DOUTFILE=$out\PolNotifier-Setup-$Version.exe" "$root\installer\PolNotifier.nsi"
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Host "OK: $out\PolNotifier-Setup-$Version.exe"
