param(
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
}

python -m pip install -r requirements.txt
$env:PYTHONPATH = Join-Path $Root "src"
python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "DevToolbox" `
    --icon "assets\devtoolbox.ico" `
    --add-data "assets\devtoolbox.ico;assets" `
    --paths "src" `
    "run.py"

Write-Host "Build complete: $Root\dist\DevToolbox.exe"
