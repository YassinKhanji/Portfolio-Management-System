#!/bin/bash
# Startup script for Portfolio Management Trading System

set -e

echo "🚀 Portfolio Management Trading System - Startup"
echo "=================================================="

# Check Python
if ! command -v python &> /dev/null; then
    echo "❌ Python not found. Please install Python 3.9+"
    exit 1
fi

echo "✓ Python $(python --version)"

# Check virtual environment
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python -m venv venv
fi

echo "✓ Activating virtual environment..."
source venv/bin/activate || . venv/Scripts/activate 2>/dev/null || true

# Install dependencies
echo "📥 Installing dependencies..."
pip install -q -r requirements.txt

# Check .env file
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found. Creating from template..."
    cp .env.example .env
    echo "📝 Please edit .env with your configuration"
    echo "   Required: DATABASE_URL, SNAPTRADE credentials, JWT_SECRET"
fi

# Create logs directory
mkdir -p logs
echo "✓ Logs directory ready (logs/)"

# Initialize database
echo "🗄️  Initializing database..."
python -c "from app.models.database import init_db; init_db()" || echo "⚠️  Database initialization skipped (already exists)"

echo ""
echo "✓ System ready!"
echo ""
echo "Start the server with:"
echo "  python app/main.py"
echo ""
echo "API Documentation:"
echo "  http://localhost:8000/docs"
