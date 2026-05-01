#!/bin/bash
#
# Tail logs from Windows production deployment (lazyboy2000.local)
#

WINDOWS_HOST="lazyboy2000.local"
LOG_DIR="~/SplintFactoryFiles/logs"
LOG_GLOB="${LOG_DIR}/processor-*.log"

echo "Tailing logs from ${WINDOWS_HOST}..."
echo "Log dir: ${LOG_DIR}"
echo ""

ssh ${WINDOWS_HOST} "latest=\$(ls -1t ${LOG_GLOB} 2>/dev/null | head -n 1); if [ -z \"\$latest\" ]; then echo 'No processor logs found.'; exit 1; fi; echo \"Log file: \$latest\"; tail -f \"\$latest\""
