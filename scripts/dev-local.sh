#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${DOTTY_LOCAL_ENV_FILE:-$ROOT_DIR/.env.local}"

# Validate the runtime before touching Docker or starting any local process.  Keeping this
# check in a standalone script makes it reusable by CI and easy to exercise with another
# `node` executable (for example an nvm/fnm managed version).
"$ROOT_DIR/scripts/check-node-version.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "未找到 $ENV_FILE。请先复制 .env.local.example 并填写数据库密码。" >&2
  exit 1
fi

if [[ ! -x "$ROOT_DIR/.venv/bin/python" ]]; then
  echo "未找到 .venv，请先安装 backend/requirements.txt。" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# 本机 MinerU 与主后端使用不同虚拟环境；显式传绝对路径，避免后端从
# backend/infrastructure/runtime 的相对目录误判为“未安装”。Docker 后端不会执行这段逻辑。
if [[ -z "${MINERU_COMMAND:-}" && -x "$ROOT_DIR/.mineru-venv/bin/mineru" ]]; then
  export MINERU_COMMAND="$ROOT_DIR/.mineru-venv/bin/mineru"
fi

cd "$ROOT_DIR"
docker compose up -d db

api_pid=""
frontend_pid=""
qwen_pid=""

cleanup() {
  set +e
  [[ -n "$qwen_pid" ]] && kill "$qwen_pid" 2>/dev/null || true
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" 2>/dev/null || true
  [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "PostgreSQL: http://127.0.0.1:${POSTGRES_PORT:-15432}"
echo "FastAPI:    http://127.0.0.1:8010"
echo "Frontend:   http://localhost:5174"
echo "Codex:      ${MODEL_PROVIDER:-mock}"
echo "MinerU:     ${MINERU_COMMAND:-auto}"
echo "Qwen TTS:   ${QWEN_TTS_URL:-http://127.0.0.1:8020}"

(
  cd "$ROOT_DIR/backend"
  exec "$ROOT_DIR/.venv/bin/python" -m uvicorn app:app --reload --port 8010
) &
api_pid=$!

(
  cd "$ROOT_DIR/frontend"
  exec npm run dev
) &
frontend_pid=$!

if [[ "${QWEN_TTS_ENABLED:-1}" == "1" && -x "$ROOT_DIR/.qwen3-tts-venv/bin/python" ]]; then
  (
    cd "$ROOT_DIR/backend"
    exec "$ROOT_DIR/.qwen3-tts-venv/bin/python" qwen_tts_service.py
  ) &
  qwen_pid=$!
else
  echo "Qwen3-TTS 未启动：设置 QWEN_TTS_ENABLED=1 且确保 .qwen3-tts-venv 存在。"
fi

wait
