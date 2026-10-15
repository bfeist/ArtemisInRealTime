param(
    [string]$Mission = 'artemis-ii',
    [ValidateRange(1, 8)][int]$Workers = 4,
    [switch]$UseSavedPlan,
    [switch]$Originals,
    [string]$OutputDir,
    [ValidateRange(1, 1000)][int]$MaxFiles,
    [ValidateRange(0, 1000000)][int]$RetryRounds = 0,
    [ValidateRange(1, 86400)][int]$RetryDelay = 300
)
$ErrorActionPreference = 'Stop'
$batchDir = Split-Path -Parent $PSScriptRoot
$repoDir = [IO.Path]::GetFullPath((Join-Path $batchDir '../..'))
$pythonExe = Join-Path $repoDir '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    $pythonExe = Join-Path $batchDir '.venv/Scripts/python.exe'
}
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Python environment is missing' }
$storage = & $pythonExe -X utf8 -c "import sys,json; sys.path.insert(0,sys.argv[1]); from config import MISSIONS; m=MISSIONS[sys.argv[2]]; print(json.dumps({'logs':str(m.data_dir/'processed'/'logs'/'ia-video'),'plan':str(m.data_dir/'processed'/'ia_video_downloads.json'),'output':str(m.raw_video_ia)}))" $batchDir $Mission
if ($LASTEXITCODE -ne 0) { throw 'Could not read mission configuration' }
$storage = $storage | ConvertFrom-Json
if ($Originals) {
    if ($Workers -gt 2) { throw 'Original downloads support at most two workers' }
    $storage.logs = Join-Path (Split-Path -Parent $storage.logs) 'ia-originals'
    $storage.plan = Join-Path (Split-Path -Parent $storage.plan) 'ia_video_originals.json'
    $originalStorage = & $pythonExe -X utf8 -c "import sys; sys.path.insert(0,sys.argv[1]); from config import MISSIONS; print(MISSIONS[sys.argv[2]].bulk_dir/'raw'/'video'/'ia_originals')" $batchDir $Mission
    if ($LASTEXITCODE -ne 0) { throw 'Could not read original storage configuration' }
    $storage.output = $originalStorage
    if ($OutputDir) { $storage.output = $OutputDir }
} elseif ($OutputDir -or $MaxFiles) {
    throw '-OutputDir and -MaxFiles require -Originals'
}
New-Item -ItemType Directory -Path $storage.logs -Force | Out-Null
$statePath = Join-Path $storage.logs 'current-process.json'
if (Test-Path -LiteralPath $statePath) {
    $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $active = Get-CimInstance Win32_Process -Filter "ProcessId = $($previous.pid)"
    if ($active -and ($active.CommandLine -like '*2b_ia_video_download*' -or $active.CommandLine -like '*2l_ia_originals*')) {
        throw "IA downloader is already running (PID $($previous.pid))"
    }
}
$stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
$stdoutPath = Join-Path $storage.logs "$stamp.stdout.log"
$stderrPath = Join-Path $storage.logs "$stamp.stderr.log"
$scriptPath = Join-Path $PSScriptRoot '2b_ia_video_download.py'
if ($Originals) { $scriptPath = Join-Path $PSScriptRoot '2l_ia_originals.py' }
$arguments = @('-X', 'utf8', '-u', ('"{0}"' -f $scriptPath), '--mission', $Mission, '--workers', $Workers,
               '--retry-rounds', $RetryRounds, '--retry-delay', $RetryDelay)
# Reuse a saved selection automatically, including retained .part files.
if (Test-Path -LiteralPath $storage.plan) { $UseSavedPlan = $true }
if ($Originals) {
    if ($UseSavedPlan) { $arguments += '--resume' }
    if ($OutputDir) { $arguments += @('--output-dir', ('"{0}"' -f $OutputDir)) }
    if ($MaxFiles) { $arguments += @('--max-files', $MaxFiles) }
} elseif ($UseSavedPlan) {
    if (-not (Test-Path -LiteralPath $storage.plan)) { throw 'No saved download plan exists' }
    $arguments += @('--download-plan', ('"{0}"' -f $storage.plan))
} else {
    $arguments += '--mission-coverage'
}
$process = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $repoDir -WindowStyle Hidden -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -PassThru
$state = @{ pid = $process.Id; startedUtc = [DateTime]::UtcNow.ToString('o'); stdout = $stdoutPath; stderr = $stderrPath; plan = $storage.plan; output = $storage.output }
$state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
$state | ConvertTo-Json
