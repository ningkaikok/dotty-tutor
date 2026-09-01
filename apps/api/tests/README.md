# 后端测试目录

这里集中存放后端测试。纯逻辑、契约和状态边界测试不连接数据库；涉及 Store、路由持久化、
迁移、外键、事务锁或并发语义的测试统一使用一次性 PostgreSQL 数据库。

## 本地运行

从仓库根目录执行默认测试发现：

```bash
cd apps/api
uv run python -m unittest discover -s tests -p 'test_*.py'
```

默认运行不会猜测 PostgreSQL 地址。没有 `DOTTY_TEST_POSTGRES_ADMIN_URL` 时，需要数据库的
测试会安全跳过，纯逻辑测试仍会运行。

运行完整后端套件时，为测试提供一个专用的 PostgreSQL **admin/维护库**（例如本地临时实例的
`postgres` 库），不要使用应用库、生产库或共享业务库：

```bash
export DOTTY_TEST_POSTGRES_ADMIN_URL='postgresql+psycopg://postgres:password@127.0.0.1:5432/postgres'
bash scripts/test-backend-postgres.sh
```

脚本只接受这个显式 admin 变量。它会创建名称形如 `dotty_ci_test_<随机后缀>` 的独立数据库，
先升级到当前 Alembic head，再运行整个 `apps/api/tests` 发现；结束时只终止自己创建的数据库
连接并删除自己前缀下的数据库，不会把 `DATABASE_URL` 当作 admin 地址，也不会输出凭据。

`PostgresTestCase` 为每个数据库测试类创建独立数据库，先升级到 Alembic head；每个测试通过
可信 schema registry 生成的 `TRUNCATE ... RESTART IDENTITY CASCADE` 清理业务表，并保留
`alembic_version`。adoption、legacy 和 preflight 测试显式创建未迁移的空数据库，不与普通 Store
测试夹具混用。`test_postgres_integration.py` 覆盖 current/head、adoption、preflight、upgrade、
verify、幂等升级、JSONB 默认值、外键 orphan 拒绝、advisory lock 和真实 Store；
`test_background_jobs.py` 另有两个 PostgreSQL 连接竞争同一任务的测试。CI 会启动独立 PostgreSQL
service 并始终设置上述 admin 变量。

测试代码仍保持单层目录，避免为当前规模引入额外测试框架；数据库行为集中在 PostgreSQL
测试设施中，纯函数和状态边界继续使用 `unittest`。
