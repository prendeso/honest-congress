#!/usr/bin/env pwsh
# Quick activation script for Honest Congress project

Write-Host "Activating virtual environment..." -ForegroundColor Cyan

# Activate venv
& "$PSScriptRoot\venv\Scripts\Activate.ps1"

Write-Host "✅ Virtual environment activated!" -ForegroundColor Green
Write-Host ""
Write-Host "Quick commands:" -ForegroundColor Yellow
Write-Host "  python -m src.cli parse --limit 100    # Parse disclosures"
Write-Host "  python -m src.cli analyze               # Run anomaly analysis"
Write-Host "  python -m src.cli performance           # Performance summary"
Write-Host "  python -m src.cli serve --port 8001     # Start web server"
Write-Host ""
Write-Host "Database status:"
python -c "from src.db.database import SessionLocal; from src.db.models import Disclosure, Asset; db = SessionLocal(); print(f'  Parsed: {db.query(Disclosure).filter(Disclosure.parsed == True).count()}/707'); print(f'  Assets: {db.query(Asset).count()}'); db.close()"

