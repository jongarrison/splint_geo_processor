#!/usr/bin/env bash
# Run the RelativeMotion dev harness against the LIVE Rhino 8 session and block until it finishes.
#
# WHY THIS SCRIPT EXISTS
#   rhinocode dispatches the script to a running Rhino instance and returns immediately (exit 0),
#   but Rhino executes out-of-band and can take 20s+ to actually run. There is no reliable stdout
#   or exit signal. So the only trustworthy "Rhino finished" signal is: delete the harness's
#   report file first, launch, then wait for the report file to REAPPEAR (the harness writes it as
#   its last step). This script encapsulates that clear-launch-wait-print dance.
#
# NOTES
#   - Always targets Rhino 8, never the PATH `rhinocode` (which is RhinoWIP / Rhino 9).
#   - Auto-detects the running Rhino 8 instance id from `rhinocode list` (verified via ps).
#   - `-r <id>` is a GLOBAL option and must come BEFORE the `script` subcommand.
#
# USAGE
#   ./run_harness.sh            # run harness_relmotion.py, wait, print report
#   TIMEOUT=240 ./run_harness.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="$HERE/harness_relmotion.py"
REPORT="$HERE/last_run_report.txt"
RC8="/Applications/Rhino 8.app/Contents/Resources/bin/rhinocode"
TIMEOUT="${TIMEOUT:-180}"   # seconds to wait for Rhino to finish

[ -x "$RC8" ] || { echo "ERROR: Rhino 8 rhinocode not found at $RC8" >&2; exit 1; }
[ -f "$HARNESS" ] || { echo "ERROR: harness not found at $HARNESS" >&2; exit 1; }

# --- find the running Rhino 8 instance id (rhinocode_remotepipe_<PID>) ---
INSTANCE=""
while read -r pid id _rest; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    if ps -p "$pid" -o args= 2>/dev/null | grep -q "/Rhino 8.app/"; then
        INSTANCE="$id"
        break
    fi
done < <("$RC8" list 2>/dev/null | tail -n +2)

if [ -z "$INSTANCE" ]; then
    echo "ERROR: no running Rhino 8 instance found. Open Rhino 8 first." >&2
    echo "Current instances:" >&2
    "$RC8" list >&2 || true
    exit 1
fi
echo "Targeting Rhino 8 instance: $INSTANCE"

# --- clear the stale report so its reappearance means THIS run finished ---
rm -f "$REPORT"

# --- dispatch (returns immediately; Rhino runs async) ---
echo "Dispatching harness (Rhino may be busy ~20s+)..."
"$RC8" -r "$INSTANCE" script "$HARNESS" || true

# --- block until the harness rewrites the report, or time out ---
waited=0
while [ ! -f "$REPORT" ]; do
    if [ "$waited" -ge "$TIMEOUT" ]; then
        echo "TIMEOUT after ${TIMEOUT}s waiting for $REPORT (Rhino may still be running)." >&2
        exit 2
    fi
    sleep 1
    waited=$((waited + 1))
done

echo "Rhino finished in ~${waited}s. Report:"
echo "------------------------------------------------------------"
cat "$REPORT"
