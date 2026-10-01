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
CONTROL_DIR="~/SplintFactoryFiles/control"
DEPLOY_REQUEST="${CONTROL_DIR}/deploy-requested"
DEPLOY_READY="${CONTROL_DIR}/deploy-ready"
DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-180}"

run_ssh() {
	local ssh_exit=0
	ssh "${WINDOWS_HOST}" "$@" || ssh_exit=$?

	if [ "${ssh_exit}" -eq 255 ]; then
		echo "" >&2
		echo "============================================================" >&2
		echo "DEPLOYMENT FAILED: SSH connection to ${WINDOWS_HOST} failed." >&2
		echo "Check the host, network, VPN, and SSH credentials, then retry." >&2
		echo "============================================================" >&2
		exit "${ssh_exit}"
	fi

	return "${ssh_exit}"
}

echo "🔄 Deploying to ${WINDOWS_HOST}..."

drain_requested=0
cleanup_drain_request() {
	if [ "${drain_requested}" -eq 1 ]; then
		run_ssh "rm -f ${DEPLOY_REQUEST} ${DEPLOY_READY}" || true
	fi
}
trap cleanup_drain_request EXIT

if [ "${SKIP_DRAIN:-0}" = "1" ]; then
	echo "WARNING: skipping activity drain. Only use this to bootstrap the drain-aware processor while confirmed idle."
elif run_ssh "powershell.exe -NoProfile -Command \"if ((Get-ScheduledTask -TaskName 'SplintGeoProcessor' -ErrorAction SilentlyContinue).State -eq 'Running') { exit 0 } else { exit 1 }\""; then
	# Replacing an old request atomically lets a retry take ownership without briefly allowing the
	# paused processor to claim another job.
	DRAIN_EXPIRES_AT=$(( $(date +%s) + DRAIN_TIMEOUT_SECONDS + 60 ))
	DRAIN_TOKEN="${DRAIN_EXPIRES_AT}:$(date +%s)-$$"
	echo "Requesting processor drain (timeout: ${DRAIN_TIMEOUT_SECONDS}s)..."
	run_ssh "mkdir -p ${CONTROL_DIR} && rm -f ${DEPLOY_READY} && printf '%s' '${DRAIN_TOKEN}' > ${DEPLOY_REQUEST}.tmp && mv ${DEPLOY_REQUEST}.tmp ${DEPLOY_REQUEST}"
	drain_requested=1
	# Stop the task in the same remote command that observes the acknowledgment. This minimizes the
	# interval in which a dropped SSH connection could leave the processor paused.
	run_ssh "deadline=\$((\$(date +%s) + ${DRAIN_TIMEOUT_SECONDS})); while true; do ready=\$(cat ${DEPLOY_READY} 2>/dev/null || true); if [ \"\$ready\" = \"${DRAIN_TOKEN}\" ]; then echo 'Processor is idle; stopping scheduled task.'; powershell.exe -NoProfile -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue' || true; exit 0; fi; if [ \"\$(date +%s)\" -ge \"\$deadline\" ]; then echo 'Timed out waiting for the processor to finish its current job.' >&2; exit 1; fi; sleep 2; done"
else
	echo "SplintGeoProcessor task is not running; clearing stale drain files and continuing."
	run_ssh "rm -f ${DEPLOY_REQUEST} ${DEPLOY_READY}"
fi

# Stop the drained scheduled task before changing any source or build files.
# Stop-ScheduledTask terminates the wrapper cmd.exe and its node child.
# `|| true` because PowerShell can return non-zero even with -ErrorAction SilentlyContinue.
echo "🛑 Stopping scheduled task and running processes..."
run_ssh "powershell.exe -NoProfile -Command 'Stop-ScheduledTask -TaskName SplintGeoProcessor -ErrorAction SilentlyContinue'" || true
sleep 2
# Belt-and-suspenders: kill any stray node that survived task stop
run_ssh "powershell.exe -NoProfile -Command 'Get-Process -Name node -ErrorAction SilentlyContinue | Stop-Process -Force'" || true
sleep 1

# Pull latest code
echo "📥 Pulling latest code..."
run_ssh "cd ${REMOTE_DIR} && git pull"

# Install dependencies and build
echo "📦 Installing dependencies and building..."
run_ssh "cd ${REMOTE_DIR} && npm install && npm run build"

# Remove the request before restart so the new processor begins polling immediately.
run_ssh "rm -f ${DEPLOY_REQUEST} ${DEPLOY_READY}"
drain_requested=0

# Restart the scheduled task
echo "♻️  Restarting SplintGeoProcessor task..."
run_ssh "powershell.exe -NoProfile -Command 'Start-ScheduledTask -TaskName SplintGeoProcessor'"

# Check status
echo "✅ Checking task status..."
run_ssh "powershell.exe -NoProfile -Command 'Get-ScheduledTaskInfo SplintGeoProcessor | Select-Object LastRunTime, LastTaskResult, TaskName'"

echo ""
echo "✨ Deployment complete! Check logs with:"
echo "   ssh ${WINDOWS_HOST} \"tail -f ~/SplintFactoryFiles/logs/processor-\$(date +%Y-%m-%d).log\""
