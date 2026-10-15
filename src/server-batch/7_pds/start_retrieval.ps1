param([switch]$UseSavedPlan)
$ErrorActionPreference = 'Stop'
$batchDir = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $batchDir '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw "Python environment is missing: $pythonExe" }
$storage = & $pythonExe -c "import sys; sys.path.insert(0, sys.argv[1]); import json; from config import PDS_ASSETS_DIR, MISSIONS; print(json.dumps({'root': str(PDS_ASSETS_DIR), 'logs': str(MISSIONS['artemis-ii'].pds_processed / 'logs')}))" $batchDir
if ($LASTEXITCODE -ne 0) { throw 'Could not read pipeline storage configuration' }
$storage = $storage | ConvertFrom-Json
$pdsRoot = $storage.root
$logDir = $storage.logs
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$stdoutPath = Join-Path $logDir "$stamp.stdout.log"
$stderrPath = Join-Path $logDir "$stamp.stderr.log"
$arguments = @('-X', 'utf8', '-u', '-m', '7_pds.retrieve', '--mission', 'artemis-ii')
if ($UseSavedPlan) { $arguments += '--use-saved-plan' }
$process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $batchDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$state = @{ pid = $process.Id; startedUtc = [DateTime]::UtcNow.ToString('o'); stdout = $stdoutPath; stderr = $stderrPath; dataRoot = $pdsRoot }
$state | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDir 'current-process.json') -Encoding utf8
$state | ConvertTo-Json
