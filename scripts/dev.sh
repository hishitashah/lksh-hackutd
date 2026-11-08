#!/usr/bin/env bash
# Start the backend and frontend together for local development (`make dev`, task T0.1).
#   backend:  FastAPI on http://localhost:8000 (auto-reloads on .py and fixtures/*.json changes)
#   frontend: Vite on http://localhost:5173 (proxies /api to the backend)
# Ctrl-C stops both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Stop every child process when this script exits or is interrupted.
trap 'kill 0' INT TERM EXIT

(cd "$ROOT/backend" && uv run uvicorn nightwatch.api:app --reload --port 8000 \
  --reload-dir . --reload-dir ../fixtures --reload-include '*.json') &
(cd "$ROOT/frontend" && npm run dev) &

wait
