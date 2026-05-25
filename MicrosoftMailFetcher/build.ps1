$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
if (Test-Path ".\dist") {
    Remove-Item -LiteralPath ".\dist" -Recurse -Force
}
python -m PyInstaller --noconfirm --onefile --windowed --name "邮件验证码助手" --icon ".\assets\mail.ico" app.py

Write-Host ""
Write-Host "Build complete: dist\邮件验证码助手.exe"
