#!/bin/bash
#
# Restart script for Windows production deployment (lazyboy2000.local)
# This script SSHs into the Windows machine, pulls latest code, rebuilds, and restarts the scheduled task
#

set -e

WINDOWS_HOST="lazyboy2000.local"
REMOTE_DIR="~/work/splint_geo_processor"

echo "🔄 Deploying to ${WINDOWS_HOST}..."

# Pull latest code
echo "📥 Pulling latest code..."
ssh ${WINDOWS_HOST} "cd ${REMOTE_DIR} && git pull"

# Install dependencies and build
echo "📦 Installing dependencies and building..."
ssh ${WINDOWS_HOST} "cd ${REMOTE_DIR} && npm install && npm run build"

# Kill any running node processes for splint_geo_processor
echo "🛑 Stopping running process..."
ssh ${WINDOWS_HOST} "powershell.exe -Command \"Get-Process node -ErrorAction SilentlyContinue | Where-Object { \\\$_.Path -like '*splint_geo_processor*' } | Stop-Process -Force\""

# Restart the scheduled task
echo "♻️  Restarting SplintGeoProcessor task..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'"

# Check status
echo "✅ Checking task status..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'"

echo ""
echo "✨ Deployment complete! Check logs with:"
echo "   ssh ${WINDOWS_HOST} \"tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log\""
