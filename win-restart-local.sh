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

# Kill any running node processes
echo "🛑 Stopping running process, if necessary..."
cmd.exe /c 'taskkill /F /IM node.exe 2>nul || echo No node.exe process found'

# Give it a moment to fully terminate
sleep 2

# Restart the scheduled task
echo "♻️  Restarting SplintGeoProcessor task..."
powershell.exe -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'

# Check status
echo "✅ Checking task status..."
powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'

echo ""
echo "✨ Restart complete! Check logs with:"
echo "   tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log"
