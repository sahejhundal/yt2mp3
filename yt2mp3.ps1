Set-Location $PSScriptRoot
Clear-Host

Write-Host "Activating virtual environment..."
.\venv\Scripts\Activate.ps1

Write-Host ""
Write-Host "Paste a YouTube URL and press Enter:"
Write-Host ""

python yt2mp3.py

deactivate

Write-Host ""
#Read-Host "Done. Press Enter to close"
