#!/bin/bash
#
# Local stop script for Windows (run this directly on the Windows machine, in Git Bash).
# Stops the SplintGeoProcessor scheduled task and kills any running wrapper/node.
#
# Use this when you want to take the processor offline without uninstalling it,
# e.g. while experimenting with Rhino on the same VM. Re-enable with:
#   bash ./win-restart-local.sh
# or:
#   powershell -NoProfile -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'
#
# To prevent it from auto-starting again at next logon, also add `-Disable`:
#   bash ./win-stop-local.sh -Disable
#
# Re-enable later with:
#   powershell -NoProfile -Command 'Enable-ScheduledTask -TaskName SplintGeoProcessor'

set -e

DISABLE=0
if [[ "${1:-}" == "-Disable" || "${1:-}" == "--disable" ]]; then
    DISABLE=1
fi

echo "🛑 Stopping SplintGeoProcessor..."

# Stop-ScheduledTask terminates the wrapper cmd.exe and its node child.
# `|| true` because PowerShell can return non-zero even with -ErrorAction SilentlyContinue.
powershell.exe -NoProfile -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue' || true
sleep 2

# Belt-and-suspenders: kill any stray node that survived task stop
powershell.exe -NoProfile -Command 'Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force' || true

if [[ $DISABLE -eq 1 ]]; then
    echo "🔒 Disabling task so it won't fire at next logon..."
    powershell.exe -NoProfile -Command 'Disable-ScheduledTask -TaskName SplintGeoProcessor' || true
fi

echo ""
echo "✅ Status:"
powershell.exe -NoProfile -Command 'Get-ScheduledTask -TaskName SplintGeoProcessor | Select-Object TaskName, State'

echo ""
if [[ $DISABLE -eq 1 ]]; then
    echo "Task disabled. Re-enable with:"
    echo "   powershell -NoProfile -Command 'Enable-ScheduledTask -TaskName SplintGeoProcessor; Start-ScheduledTask -TaskName SplintGeoProcessor'"
else
    echo "Task stopped (still enabled - will run at next logon). Restart with:"
    echo "   bash ./win-restart-local.sh"
fi
