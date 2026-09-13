#!/usr/bin/env bash
# Launch hospital-control (the local control panel). Stays in the foreground and prints the URL.
#   ./webapp/run_web.sh            # http://127.0.0.1:8767
#   ./webapp/run_web.sh --port 9000
set -e
cd "$(dirname "$0")/.."
export PYTHONUNBUFFERED=1
exec python3 webapp/server.py "$@"
