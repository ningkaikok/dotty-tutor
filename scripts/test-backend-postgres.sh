#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${DOTTY_TEST_POSTGRES_ADMIN_URL:-}" ]]; then
  echo "未设置 DOTTY_TEST_POSTGRES_ADMIN_URL；拒绝连接默认或共享数据库。" >&2
  exit 2
fi

cd "$ROOT_DIR/apps/api"
exec uv run python -m tests.postgres_test_runner
