#!/bin/bash
#
# Re-enable and start the Windows SplintGeoProcessor task
# This restores normal auto-restart behavior after debugging
#

set -e

WINDOWS_HOST="lazyboy2000.local"

echo "▶️  Starting SplintGeoProcessor on ${WINDOWS_HOST}..."

# Enable the task to restore auto-restart
echo "✅ Enabling auto-restart..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Enable-ScheduledTask -TaskName SplintGeoProcessor'"

# Start the scheduled task
echo "🚀 Starting scheduled task..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'"

# Give it a moment to start
sleep 2

# Check status
echo "📊 Checking task status..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Get-ScheduledTask SplintGeoProcessor | Select-Object TaskName, State'"
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult'"

echo ""
echo "✨ SplintGeoProcessor enabled and started."
echo "   The task will now auto-restart on reboot or crashes."
echo ""
echo "📝 Check logs with:"
echo "   ssh ${WINDOWS_HOST} \"tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log\""
