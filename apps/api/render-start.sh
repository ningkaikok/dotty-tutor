#!/bin/sh
set -eu

python -m persistence.migration_cli upgrade
python -m worker --registry routers.textbook_routes:textbook_job_registry &
worker_pid=$!

cleanup() {
  kill "$worker_pid" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

python -m uvicorn app:app --host 0.0.0.0 --port "${PORT:-10000}" --workers 1 &
api_pid=$!
wait "$api_pid"
