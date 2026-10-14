#!/usr/bin/env bash
# Run the backend suite in parallel workers against disposable PostgreSQL databases.
#
# Usage: scripts/test-backend-postgres.sh [tests.test_x ...] [--workers N]
# Set DOTTY_TEST_COVERAGE=1 to collect a coverage report (CI does this on pushes to main).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${DOTTY_TEST_POSTGRES_ADMIN_URL:-}" ]]; then
  echo "未设置 DOTTY_TEST_POSTGRES_ADMIN_URL；拒绝连接默认或共享数据库。" >&2
  exit 2
fi

cd "$ROOT_DIR/apps/api"

if [[ "${DOTTY_TEST_COVERAGE:-0}" != "1" ]]; then
  exec uv run python -m tests.postgres_test_runner "$@"
fi

# 覆盖率只出报告，不影响退出码：先看真实分布，未来再决定是否按模块设基线。
# 测试在多个子进程里运行，pyproject 中 parallel/concurrency 让每个子进程各写一份
# 数据，结束后合并再出报告。
uv run coverage erase
set +e
uv run coverage run -m tests.postgres_test_runner "$@"
test_status=$?
set -e

uv run coverage combine --quiet || true
uv run coverage report -m || true

exit "$test_status"
