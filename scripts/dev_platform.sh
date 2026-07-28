#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
AGENT_PORT="${AGENT_PORT:-7862}"

pick_free_port() {
  local start_port="$1"
  python3 - "$start_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
while True:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            port += 1
            continue
    print(port)
    break
PY
}

BACKEND_PORT="$(pick_free_port "${BACKEND_PORT}")"
FRONTEND_PORT="$(pick_free_port "${FRONTEND_PORT}")"
AGENT_PORT="$(pick_free_port "${AGENT_PORT}")"

load_env_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
  fi
}

load_env_file "${ROOT}/.env"
load_env_file "${ROOT}/.env.local"

# This script owns the Agent Web process below, so the backend proxy must use
# the same dynamically selected port instead of a stale URL from .env.local.
export CLAWD_AGENT_WEB_URL="http://127.0.0.1:${AGENT_PORT}"
export CLAWD_WEB_PORT="${AGENT_PORT}"
export CLAWD_DEFAULT_PROVIDER="${CLAWD_DEFAULT_PROVIDER:-ark}"
export CLAWD_MAX_CONCURRENT_AGENT_RUNS="${CLAWD_MAX_CONCURRENT_AGENT_RUNS:-64}"
export CLAWD_AGENT_QUEUE_TIMEOUT_SECONDS="${CLAWD_AGENT_QUEUE_TIMEOUT_SECONDS:-5}"
export CLAWD_MAX_TOOL_CALLS_PER_RUN="${CLAWD_MAX_TOOL_CALLS_PER_RUN:-24}"
export CLAWD_ENABLE_WEBSEARCH="${CLAWD_ENABLE_WEBSEARCH:-true}"
export CLAWD_WEBSEARCH_PROVIDER="${CLAWD_WEBSEARCH_PROVIDER:-ark_responses}"
export CLAWD_SUBJECTS_WORKSPACE="${ROOT}"
export CLAWD_OUTPUT_DIR="${ROOT}/agent_runtime/outputs"
export SUBJECTS_DATABASE_URL="${SUBJECTS_DATABASE_URL:-${DATABASE_URL:-mysql+pymysql://chassis:chassis_dev_password@127.0.0.1:3306/chassis_platform}}"
export RAG_PROVIDER="${RAG_PROVIDER:-platform}"
export RAG_PLATFORM_BASE_URL="${RAG_PLATFORM_BASE_URL:-http://127.0.0.1:${BACKEND_PORT}}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export AGENT_JOB_BROKER="${AGENT_JOB_BROKER:-redis}"
export AGENT_JOB_ROLE="${AGENT_JOB_ROLE:-all}"
export AGENT_WORKER_CONCURRENCY="${AGENT_WORKER_CONCURRENCY:-64}"
export AGENT_MODEL_CONCURRENCY_ARK="${AGENT_MODEL_CONCURRENCY_ARK:-64}"
# Semantic progress/replan/completion gates are the normal stopping mechanism.
# This is only a distant emergency boundary for genuinely pathological runs.
export AGENT_JOB_DEFAULT_MAX_TURNS="${AGENT_JOB_DEFAULT_MAX_TURNS:-24}"
export AGENT_DISPATCH_LEASE_TTL_MS="${AGENT_DISPATCH_LEASE_TTL_MS:-120000}"
export AGENT_SESSION_LOCK_TTL_MS="${AGENT_SESSION_LOCK_TTL_MS:-120000}"

mkdir -p "${ROOT}/agent_runtime/outputs"

cleanup() {
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend: http://127.0.0.1:${BACKEND_PORT}"
(
  cd "${ROOT}"
  DATA_BACKEND="${DATA_BACKEND:-mysql}" \
  DATABASE_URL="${DATABASE_URL:-mysql+pymysql://chassis:chassis_dev_password@127.0.0.1:3306/chassis_platform}" \
  CLAWD_AGENT_WEB_URL="${CLAWD_AGENT_WEB_URL}" \
  FRONTEND_PORT="${FRONTEND_PORT}" \
  python3 -m uvicorn backend.app.main:app --host 127.0.0.1 --port "${BACKEND_PORT}"
) &

echo "Starting agent web: http://127.0.0.1:${AGENT_PORT}"
(
  cd "${ROOT}/agent_runtime"
  python3 web_app.py
) &

echo "Starting frontend: http://127.0.0.1:${FRONTEND_PORT}"
(
  cd "${ROOT}/frontend"
  PLATFORM_API_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" \
  VITE_API_BASE_URL="http://127.0.0.1:${BACKEND_PORT}" \
  npm run dev -- --host 127.0.0.1 --port "${FRONTEND_PORT}"
) &

wait
