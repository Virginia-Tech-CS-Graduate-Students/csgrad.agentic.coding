# The PowerShell entry point also works on systems that block unsigned pip .exe launchers.
$transcriptPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $transcriptPython)) { throw 'Run setup.ps1 first.' }
& $transcriptPython -m source_to_transcript.cli @args
exit $LASTEXITCODE
