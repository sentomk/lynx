# CDPC entry point owned and committed by the Lynx consumer.
$CdpcLynxRoot = Split-Path -Parent $PSScriptRoot
$CdpcPython = Join-Path $CdpcLynxRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $CdpcPython)) { $CdpcPython = "python" }
& (Join-Path $PSScriptRoot "env.ps1") $CdpcPython -B (Join-Path $PSScriptRoot "cdpc.py") @args
exit $LASTEXITCODE
