#!/bin/bash
#
# Stop the Windows SplintGeoProcessor task and disable auto-restart
# This is for debugging purposes - use win-ghdebug-start.sh to re-enable
#

set -e

# Load target host config - edit win-env-set.sh to switch targets
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/win-env-set.sh"

echo "🛑 Stopping SplintGeoProcessor on ${WINDOWS_HOST}..."

# Stop the scheduled task
echo "⏸️  Stopping scheduled task..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor'"

# Disable the task to prevent auto-restart
echo "🚫 Disabling auto-restart..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Disable-ScheduledTask -TaskName SplintGeoProcessor'"

# Kill any running node processes to be thorough
echo "🔪 Killing any remaining node.exe processes..."
ssh ${WINDOWS_HOST} "cmd.exe /c 'taskkill /F /IM node.exe 2>nul || echo No node.exe process found'"

# Check status
echo "✅ Checking task status..."
ssh ${WINDOWS_HOST} "powershell.exe -Command 'Get-ScheduledTask SplintGeoProcessor | Select-Object TaskName, State'"

echo ""
echo "✨ SplintGeoProcessor stopped and disabled."
echo "   The task will NOT auto-restart on reboot or crashes."
echo "   Use win-ghdebug-start.sh to re-enable and start it."
