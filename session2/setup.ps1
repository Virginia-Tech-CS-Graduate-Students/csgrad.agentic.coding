[CmdletBinding()]
param([switch]$SkipModel)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Install Python 3.14 (64-bit) with the Python launcher, then run setup.ps1 again.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.12 or later, then run setup.ps1 again.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & py -3.14 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.14 environment.' }
}
$transcriptPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $transcriptPython -m pip install --cache-dir .cache\pip -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& $transcriptPython -m pip install --no-deps --no-build-isolation -e .
if ($LASTEXITCODE -ne 0) { throw 'Local package installation failed.' }
Push-Location -LiteralPath ui
try {
    & npm.cmd ci --no-audit --no-fund --cache ..\.cache\npm
    if ($LASTEXITCODE -ne 0) { throw 'UI dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'UI build failed.' }
} finally { Pop-Location }
if (-not $SkipModel) {
    $env:HF_HUB_DISABLE_TELEMETRY = '1'
    & $transcriptPython -m server.download_model
    if ($LASTEXITCODE -ne 0) { throw 'Model download failed. Run setup.ps1 again to retry.' }
}
Write-Host 'Setup complete. Run .\launch.ps1 to open Local Transcript.'
