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

# Stop the scheduled task first so the wrapper doesn't relaunch node mid-deploy
echo "🛑 Stopping scheduled task and running processes..."
powershell.exe -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue'
sleep 2
# Kill both the wrapper (cmd.exe running run-processor) and node
cmd.exe /c 'taskkill /F /FI "IMAGENAME eq cmd.exe" /FI "WINDOWTITLE eq run-processor*" 2>nul & taskkill /F /IM node.exe 2>nul & exit 0'
sleep 2

# Restart the scheduled task (relaunches the wrapper, which relaunches node)
echo "♻️  Restarting SplintGeoProcessor task..."
powershell.exe -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'

# Check status
echo "✅ Checking task status..."
powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'

echo ""
echo "✨ Restart complete! Check logs with:"
echo "   tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log"
