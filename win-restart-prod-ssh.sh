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

# Kill any running node processes
# Need to use cmd.exe since SSH opens Git Bash by default
echo "🛑 Stopping running process..."
ssh ${WINDOWS_HOST} "cmd.exe /c 'taskkill /F /IM node.exe 2>nul || echo No node.exe process found'"
# Give it a moment to fully terminate
sleep 2

# Restart the scheduled task
echo "♻️  Restarting SplintGeoProcessor task..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'"

# Check status
echo "✅ Checking task status..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'"

echo ""
echo "✨ Deployment complete! Check logs with:"
echo "   ssh ${WINDOWS_HOST} \"tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log\""
