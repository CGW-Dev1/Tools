$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
if (Test-Path ".\dist") {
    Get-ChildItem -Path ".\dist" -Filter "*.exe" -File | Remove-Item -Force
}
python -m PyInstaller --noconfirm --onefile --windowed --name "微软邮件获取" --icon ".\assets\mail.ico" app.py

Write-Host ""
Write-Host "Build complete: dist\微软邮件获取.exe"
