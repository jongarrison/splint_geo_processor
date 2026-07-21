#!/usr/bin/env bash
# Thin wrapper: dispatch THIS splint's harness.py via the shared _devkit dispatcher.
# All the Rhino-instance-discovery / dispatch / wait-for-report logic lives in
# generators/dev/_devkit/run_harness.sh (splint-agnostic); this file just points it at the
# harness.py sitting next to it.
#
# USAGE
#   ./run.sh
#   TIMEOUT=240 ./run.sh
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/../_devkit/run_harness.sh" "$HERE/harness.py"
