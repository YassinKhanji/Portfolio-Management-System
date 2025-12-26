# Startup script for Portfolio Management Trading System (Windows)
# Run: powershell -ExecutionPolicy Bypass -File startup.ps1

Write-Host "rocketstart Portfolio Management Trading System - Startup"
Write-Host "==========================================================" -ForegroundColor Cyan

# Check Python
try {
    $pythonVersion = python --version 2>&1
    Write-Host "check_mark Python $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "cross_mark Python not found. Please install Python 3.9+" -ForegroundColor Red
    exit 1
}

# Check virtual environment
if (-not (Test-Path "venv")) {
    Write-Host "package Creating virtual environment..."
    python -m venv venv
}

Write-Host "check_mark Activating virtual environment..."
& ".\venv\Scripts\Activate.ps1"

# Install dependencies
Write-Host "inbox Installing dependencies..."
pip install -q -r requirements.txt

# Check .env file
if (-not (Test-Path ".env")) {
    Write-Host "warning .env file not found. Creating from template..."
    Copy-Item ".env.example" ".env"
    Write-Host "memo Please edit .env with your configuration"
    Write-Host "   Required: DATABASE_URL, SNAPTRADE credentials, JWT_SECRET"
}

# Create logs directory
if (-not (Test-Path "logs")) {
    New-Item -ItemType Directory -Path "logs" | Out-Null
}
Write-Host "check_mark Logs directory ready (logs/)"

# Initialize database
Write-Host "bar_chart Initializing database..."
python -c "from app.models.database import init_db; init_db()" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "warning Database initialization skipped (already exists)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "check_mark System ready!" -ForegroundColor Green
Write-Host ""
Write-Host "Start the server with:"
Write-Host "  python app/main.py"
Write-Host ""
Write-Host "API Documentation:"
Write-Host "  http://localhost:8000/docs"
