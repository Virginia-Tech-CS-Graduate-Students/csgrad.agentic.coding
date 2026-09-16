[CmdletBinding()]
param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$transcriptPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $transcriptPython) -or -not (Test-Path -LiteralPath 'ui\dist\index.html')) {
    throw 'Run .\setup.ps1 before starting the app.'
}
$existing = $null
try { $existing = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 } catch {}
if ($existing.app -eq 'local-transcript') {
    if (-not $NoBrowser) { Start-Process 'http://127.0.0.1:8765' }
    Write-Host 'Local Transcript is already running at http://127.0.0.1:8765'
    return
}
$browserTask = $null
if (-not $NoBrowser) {
    $browserTask = Start-Job {
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            try {
                $result = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 1
                if ($result.app -eq 'local-transcript') { Start-Process 'http://127.0.0.1:8765'; return }
            } catch {}
            Start-Sleep -Seconds 1
        }
    }
}
Write-Host 'Local Transcript: http://127.0.0.1:8765. Press Ctrl+C here to stop.'
try {
    & $transcriptPython -m uvicorn server.app:app --host 127.0.0.1 --port 8765 --workers 1
} finally {
    if ($browserTask) { Stop-Job $browserTask; Remove-Job $browserTask }
}
