$ErrorActionPreference = "Stop"

$AppName = -join ([char[]](0x90AE, 0x4EF6, 0x9A8C, 0x8BC1, 0x7801, 0x52A9, 0x624B))

python -m pip install -r requirements.txt
if (Test-Path ".\dist") {
    Remove-Item -LiteralPath ".\dist" -Recurse -Force
}
python -m PyInstaller --noconfirm --onefile --windowed --name $AppName --icon ".\assets\mail.ico" app.py

Write-Host ""
Write-Host ("Build complete: dist\" + $AppName + ".exe")
