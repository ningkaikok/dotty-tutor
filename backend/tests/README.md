# 后端测试目录

这里集中存放后端测试，避免测试文件与应用模块混在同一层。

当前先保持单层目录，原因是测试数量仍然适中，且所有测试共享同一套临时 SQLite、FastAPI
TestClient 和 Mock Runtime。后续只有在测试数量或执行时间明显增长时，才按职责拆成：

- `unit/`：纯函数、题目结构、公式和质量门禁测试；
- `integration/`：Store、路由、数据库和应用装配测试；
- `contract/`：模型响应、OpenAPI 和运行审计契约测试。

测试从 `backend/` 目录运行，保留当前模块导入方式：

```bash
cd backend
../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

生产代码的分层迁移应单独提交，不要在移动测试时同时改变路由、领域服务或 Store 的行为。
