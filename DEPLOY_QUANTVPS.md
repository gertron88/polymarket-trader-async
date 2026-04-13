# QuantVPS Deployment Guide

## Quick Setup (Manual)

Since automated SSH with password auth is limited, here's the manual deployment process:

### Step 1: SSH into VPS
```bash
ssh root@91.250.249.35
# Password: StrangeStronger15+
```

### Step 2: Run Setup Script
```bash
# On the VPS, run:
apt-get update && apt-get install -y python3 python3-pip python3-venv git

# Create directory
mkdir -p /opt/polymarket-trader
cd /opt/polymarket-trader

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install aiohttp websockets pyyaml py-clob-client python-dotenv structlog
```

### Step 3: Upload Bot Code
From your local machine:
```bash
rsync -avz --exclude='.git' --exclude='__pycache__' \
    /home/gertron/polymarket-trader-async/ \
    root@91.250.249.35:/opt/polymarket-trader/
```

### Step 4: Create Environment File
On the VPS:
```bash
cd /opt/polymarket-trader
nano .env
```

Paste the credentials from `.env.vps`

### Step 5: Run in Dry-Run Mode
```bash
cd /opt/polymarket-trader
source venv/bin/activate
python3 -m src.main
```

## Security

After setup, consider:
- Change password: `passwd`
- Create non-root user
- Enable SSH keys only
