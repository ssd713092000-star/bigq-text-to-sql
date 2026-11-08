<#
Local stack helper.   .\scripts\dev.ps1 <command>
  up       build + start everything (detached), then show status
  down     stop (keeps the database volume)
  reset    stop and DELETE the local database volume
  logs     follow logs (optionally: .\scripts\dev.ps1 logs api)
  ps       service status
  migrate  re-run DB migrations
  test     ruff + pytest (workspace env)
  seed     register the NYC taxi demo dataset + 20 examples      (phase 4)
  evals    run the 10-question eval suite against the stack       (phase 8)
#>
param([Parameter(Position = 0)][string]$Command = "up", [Parameter(Position = 1)][string]$Service = "")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Compose = @("compose", "-f", "$Root\infra\docker-compose.yaml", "--env-file", "$Root\infra\.env")

if (-not (Test-Path "$Root\infra\.env")) { throw "Create infra\.env from infra\.env.example first." }

switch ($Command) {
    "up"      { docker @Compose up --build -d; if ($?) { docker @Compose ps } }
    "down"    { docker @Compose down }
    "reset"   { docker @Compose down -v }
    "logs"    { if ($Service) { docker @Compose logs -f $Service } else { docker @Compose logs -f } }
    "ps"      { docker @Compose ps }
    "migrate" { docker @Compose run --rm migrate }
    "test"    { Push-Location $Root; uv run ruff check api mcp_server; if ($?) { uv run pytest -q }; Pop-Location }
    "seed"    { throw "seed arrives in phase 4" }
    "evals"   { throw "evals against the stack arrive in phase 8" }
    default   { Get-Help $PSCommandPath; exit 1 }
}
