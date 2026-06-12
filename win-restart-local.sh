#!/bin/bash
#
# Local restart script for Windows (run this directly on the Windows machine)
# Pulls latest code, rebuilds, and restarts the SplintGeoProcessor service
#

set -e

echo "🔄 Restarting SplintGeoProcessor locally..."

# Navigate to the project directory
cd ~/work/splint_geo_processor

# Pull latest code
echo "📥 Pulling latest code..."
git pull

# Install dependencies and build
echo "📦 Installing dependencies and building..."
npm install
npm run build

# Stop the scheduled task first so the wrapper doesn't relaunch node mid-deploy.
# Stop-ScheduledTask terminates the wrapper cmd.exe (and its node child).
echo "🛑 Stopping scheduled task and running processes..."
powershell.exe -NoProfile -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue'
sleep 2
# Belt-and-suspenders: kill any stray node that survived task stop
powershell.exe -NoProfile -Command 'Stop-Process -Name node -Force -ErrorAction SilentlyContinue'
sleep 1

# Restart the scheduled task (relaunches the wrapper, which relaunches node)
echo "♻️  Restarting SplintGeoProcessor task..."
powershell.exe -NoProfile -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'

# Check status
echo "✅ Checking task status..."
powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'

echo ""
echo "✨ Restart complete! Check logs with:"
echo "   tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log"
