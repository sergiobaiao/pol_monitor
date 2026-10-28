# Lança uma nova versão: incrementa a última tag vX.Y.Z, cria a tag e envia ao GitHub.
# O CI publica a release do desktop (instalador NSIS, marcada como latest) e faz o deploy da web.
# Uso: .\release.ps1 [-Bump patch|minor|major] [-DryRun]
param(
    [ValidateSet("patch", "minor", "major")] [string]$Bump = "patch",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Invoke-Git { $out = & git.exe @args; if ($LASTEXITCODE) { throw "git $args falhou" }; $out }

# Só lança o que já está commitado e enviado em main
if ((Invoke-Git rev-parse --abbrev-ref HEAD) -ne "main") { throw "Lance a partir da branch main." }
if (Invoke-Git status --porcelain --untracked-files=no) { throw "Há alterações não commitadas." }
Invoke-Git fetch --quiet --tags origin
if ((Invoke-Git rev-parse HEAD) -ne (Invoke-Git rev-parse origin/main)) { throw "main local difere de origin/main: faça pull/push antes." }

$last = Invoke-Git tag --list "v*" --sort=-v:refname | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' } | Select-Object -First 1
$v = if ($last) { [version]$last.TrimStart("v") } else { [version]"0.0.0" }
$next = switch ($Bump) {
    "major" { "{0}.0.0" -f ($v.Major + 1) }
    "minor" { "{0}.{1}.0" -f $v.Major, ($v.Minor + 1) }
    default { "{0}.{1}.{2}" -f $v.Major, $v.Minor, ($v.Build + 1) }
}
$tag = "v$next"
if (Invoke-Git tag --points-at HEAD --list "v*") { throw "O commit atual já tem tag de versão; nada novo para lançar." }

Write-Host "Última versão: $(if ($last) { $last } else { '(nenhuma)' })  ->  nova: $tag"
if ($DryRun) { Write-Host "(simulação: nada foi criado)"; exit 0 }

Invoke-Git tag -a $tag -m "POL ADV Notifier $next"
Invoke-Git push --quiet origin $tag
Write-Host "Tag $tag enviada. Acompanhe: https://github.com/sergiobaiao/pol_monitor/actions"
Write-Host "Instalador: https://github.com/sergiobaiao/pol_monitor/releases/tag/$tag"
