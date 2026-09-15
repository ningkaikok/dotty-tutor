#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${DOTTY_TEST_POSTGRES_ADMIN_URL:-}" ]]; then
  echo "未设置 DOTTY_TEST_POSTGRES_ADMIN_URL；拒绝连接默认或共享数据库。" >&2
  exit 2
fi

cd "$ROOT_DIR/apps/api"

# 覆盖率只出报告，不影响退出码：先看真实分布，未来再决定是否按模块设基线。
set +e
uv run coverage run -m tests.postgres_test_runner
test_status=$?
set -e

uv run coverage report -m || true

exit "$test_status"
