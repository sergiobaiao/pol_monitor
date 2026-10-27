# Compila a edição desktop (Debug por padrão). Uso: .\build.ps1 [-Configuration Release]
param([string]$Configuration = "Debug")
$ErrorActionPreference = "Stop"
dotnet build "$PSScriptRoot\PolNotifier.sln" -c $Configuration
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Write-Host "OK: $PSScriptRoot\PolNotifier\bin\$Configuration\net10.0-windows\PolNotifier.exe"
