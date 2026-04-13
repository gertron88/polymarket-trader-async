#!/bin/bash
# Sync local trading bot code to QuantVPS
# Run this from your local machine

VPS_IP="91.250.249.35"
VPS_USER="root"
LOCAL_DIR="/home/gertron/polymarket-trader-async/"
REMOTE_DIR="/opt/polymarket-trader/"

echo "Syncing trading bot to QuantVPS..."
echo "VPS: $VPS_USER@$VPS_IP"
echo ""

# Create remote directory structure
echo "Creating remote directory..."
ssh $VPS_USER@$VPS_IP "mkdir -p $REMOTE_DIR"

# Sync files (excluding .git and __pycache__)
echo "Uploading bot code..."
rsync -avz --progress \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    "$LOCAL_DIR" \
    "$VPS_USER@$VPS_IP:$REMOTE_DIR"

echo ""
echo "Upload complete!"
echo ""
echo "Next: SSH into the VPS and create the .env file:"
echo "  ssh root@$VPS_IP"
echo "  cd $REMOTE_DIR"
echo "  nano .env"
echo ""
