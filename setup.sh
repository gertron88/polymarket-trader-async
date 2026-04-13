#!/bin/bash
# Setup script for Polymarket Trading Bot
# Run this after cloning to configure the bot

set -e

echo "🔧 Polymarket Trading Bot Setup"
echo "================================"
echo ""

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $python_version"

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "📦 Activating virtual environment..."
source venv/bin/activate

# Install requirements
echo "📦 Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Check if .env exists
if [ ! -f ".env" ]; then
    echo ""
    echo "⚠️  Environment file not found!"
    echo ""
    echo "Creating .env from template..."
    cp .env.example .env
    echo ""
    echo "🚨 IMPORTANT: Edit .env with your actual credentials before running!"
    echo "   1. Open .env in your editor"
    echo "   2. Fill in your Polymarket API credentials"
    echo "   3. Save the file"
    echo ""
fi

# Create data directory
mkdir -p data

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your credentials"
echo "  2. Test with: python -c 'from src.execution.clob_client import PolymarketClobClient; c = PolymarketClobClient(); c.load_credentials(); print(\"OK\")'"
echo "  3. Start trading: python src/main.py"
echo ""
