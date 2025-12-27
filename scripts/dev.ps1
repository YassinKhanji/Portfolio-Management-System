param(
  [int]$Port = 8000,
  [string]$BindHost = "127.0.0.1"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
  $uvLocal = Join-Path $repoRoot ".venv/Scripts/uv.exe"
  if (Test-Path $uvLocal) { $uv = $uvLocal } else { $uv = "uv" }

  $envRel = ".env"
  if (-not (Test-Path $envRel)) {
    Write-Warning ".env not found at $(Join-Path $repoRoot $envRel); proceeding without --env-file"
    & $uv run uvicorn app.main:app --reload --host $BindHost --port $Port
  }
  else {
    & $uv run --env-file "$envRel" uvicorn app.main:app --reload --host $BindHost --port $Port
  }
}
finally {
  Pop-Location
}
