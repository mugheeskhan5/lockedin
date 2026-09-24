#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_root"
mkdir -p backend/data/logs
# Keep the last launcher output. Python separately rotates the durable summary
# log and retains per-stage diagnostic files for 30 days.
exec "$project_root/.venv/bin/python" -u -m backend.nightly "$@" > backend/data/logs/cron-last.log 2>&1
