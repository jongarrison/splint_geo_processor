#!/bin/bash
#
# Restart script for Windows production deployment.
# SSHes into the Windows machine, pulls latest code, rebuilds, and restarts the
# scheduled task. Target host is set in win-env-set.sh.
#

set -e

# Load target host config - edit win-env-set.sh to switch targets
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/win-env-set.sh"
REMOTE_DIR="~/work/splint_geo_processor"

echo "🔄 Deploying to ${WINDOWS_HOST}..."

# Pull latest code
echo "📥 Pulling latest code..."
ssh ${WINDOWS_HOST} "cd ${REMOTE_DIR} && git pull"

# Install dependencies and build
echo "📦 Installing dependencies and building..."
ssh ${WINDOWS_HOST} "cd ${REMOTE_DIR} && npm install && npm run build"

# Stop the scheduled task first so the wrapper doesn't relaunch node mid-deploy.
# Stop-ScheduledTask terminates the wrapper cmd.exe and its node child.
# `|| true` because PowerShell can return non-zero even with -ErrorAction SilentlyContinue.
echo "🛑 Stopping scheduled task and running processes..."
ssh ${WINDOWS_HOST} "powershell.exe -NoProfile -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue'" || true
sleep 2
# Belt-and-suspenders: kill any stray node that survived task stop
ssh ${WINDOWS_HOST} "powershell.exe -NoProfile -Command 'Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force'" || true
sleep 1

# Restart the scheduled task
echo "♻️  Restarting SplintGeoProcessor task..."
ssh ${WINDOWS_HOST} "powershell.exe -NoProfile -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'"

# Check status
echo "✅ Checking task status..."
ssh ${WINDOWS_HOST} "powershell.exe -NoProfile -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'"

echo ""
echo "✨ Deployment complete! Check logs with:"
echo "   ssh ${WINDOWS_HOST} \"tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log\""
