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

# Stop the scheduled task first so the wrapper doesn't relaunch node
echo "🛑 Stopping running process, if necessary..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue'"
sleep 2
# Kill any remaining processes (wrapper cmd loop and child node)
ssh ${WINDOWS_HOST} "cmd.exe /c 'taskkill /F /FI \"IMAGENAME eq cmd.exe\" /FI \"WINDOWTITLE eq run-processor*\" 2>nul & taskkill /F /IM node.exe 2>nul & exit 0'"
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
