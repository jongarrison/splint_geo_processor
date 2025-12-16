#!/bin/bash
#
# Tail logs from Windows production deployment (lazyboy2000.local)
#

WINDOWS_HOST="lazyboy2000.local"
LOG_DIR="~/SplintFactoryFiles/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/processor-${TODAY}.log"

echo "📋 Tailing logs from ${WINDOWS_HOST}..."
echo "Log file: ${LOG_FILE}"
echo ""

ssh ${WINDOWS_HOST} "tail -f ${LOG_FILE}"
