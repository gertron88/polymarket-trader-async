#!/bin/bash
# QuantVPS Deployment Script for Polymarket Trading Bot
# Run this on the VPS as root

set -e

echo "=========================================="
echo "Polymarket Trading Bot - VPS Setup"
echo "=========================================="

# Update system
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git htop curl

# Create trading bot directory
echo "[2/8] Creating bot directory..."
mkdir -p /opt/polymarket-trader
cd /opt/polymarket-trader

# Create virtual environment
echo "[3/8] Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
echo "[4/8] Upgrading pip..."
pip install --quiet --upgrade pip

# Install dependencies
echo "[5/8] Installing Python dependencies..."
pip install --quiet \
    aiohttp>=3.8.0 \
    websockets>=11.0 \
    pyyaml>=6.0 \
    py-clob-client>=0.8.0 \
    python-dotenv>=1.0.0 \
    structlog

echo "[6/8] Dependencies installed."
echo ""
echo "=========================================="
echo "NEXT STEPS:"
echo "=========================================="
echo ""
echo "1. Upload the bot code to /opt/polymarket-trader/"
echo "   Use: scp -r /path/to/polymarket-trader-async/* root@91.250.249.35:/opt/polymarket-trader/"
echo ""
echo "2. Create the .env file:"
echo "   nano /opt/polymarket-trader/.env"
echo ""
echo "3. Test the installation:"
echo "   cd /opt/polymarket-trader"
echo "   source venv/bin/activate"
echo "   python3 -c 'from src.execution.clob_client import get_clob_client; print(\"OK\")'"
echo ""
echo "4. Run in dry-run mode:"
echo "   python3 -m src.main"
echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
