# 部署与运维

本文提供当前单机 MVP 的部署方法，并说明升级为公网生产架构前必须补齐的能力。

## 部署边界

GitHub 负责保存源码和运行 CI，不会直接运行 FastAPI、PostgreSQL、MinerU 或 Qwen3-TTS。
推荐将前端构建为静态文件，后端运行在 Linux 主机或容器平台，数据库使用托管 PostgreSQL。

当前仓库包含后端 Dockerfile，但尚未包含 Alembic、对象存储和任务队列，因此以下方案适合
单机、单进程和受控用户内测。当前实现包含进程内状态，暂时只能使用一个 Uvicorn worker。

```text
浏览器
  → HTTPS / Nginx 或负载均衡
  → 前端静态文件 + /api 反向代理
  → FastAPI（1 worker）
       ├─ PostgreSQL
       ├─ 持久化磁盘
       └─ 独立模型、OCR 和 TTS 服务
```

目标生产架构应增加对象存储、Redis 和 OCR/出题后台 worker。详细优先级见
[路线图](roadmap.md)。

## 服务器准备

以下示例使用 Ubuntu 22.04/24.04、Python 3.12、Node.js 20+、Nginx 和 PostgreSQL。
示例路径、用户和域名需要替换为实际值。

```bash
sudo apt update
sudo apt install -y git nginx postgresql postgresql-client rsync python3.12 python3.12-venv
sudo adduser --system --group --home /opt/dotty-tutor dotty
sudo mkdir -p /opt/dotty-tutor /srv/dotty-tutor/data
sudo chown -R dotty:dotty /opt/dotty-tutor /srv/dotty-tutor
sudo -u dotty git clone https://github.com/ningkaikok/dotty-tutor.git /opt/dotty-tutor
```

本机 PostgreSQL 示例：

```bash
sudo -u postgres createuser --pwprompt dotty_app
sudo -u postgres createdb -O dotty_app dotty_tutor
```

使用云数据库时，应创建低权限应用账号、启用 SSL 并限制网络白名单。数据库密码中的特殊
字符需要 URL 编码。不要让 PostgreSQL 直接暴露到公网。

## 后端安装

```bash
sudo -u dotty python3.12 -m venv /opt/dotty-tutor/.venv
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install --upgrade pip
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install \
  -r /opt/dotty-tutor/backend/requirements.txt

sudo install -d -o dotty -g dotty /etc/dotty-tutor
sudo touch /etc/dotty-tutor/backend.env
sudo chown dotty:dotty /etc/dotty-tutor/backend.env
sudo chmod 600 /etc/dotty-tutor/backend.env
```

`/etc/dotty-tutor/backend.env` 示例：

```dotenv
DATABASE_URL=postgresql+psycopg://dotty_app:replace-with-url-encoded-password@db.example.com:5432/dotty_tutor?sslmode=require
DOTTY_DATA_DIR=/srv/dotty-tutor/data
CORS_ORIGINS=https://tutor.example.com
TRUSTED_HOSTS=tutor.example.com

MODEL_PROVIDER=ollama
MODEL_NAME=qwen2.5:3b
OLLAMA_BASE_URL=http://127.0.0.1:11434
MINERU_COMMAND=/opt/dotty-tutor/.mineru-venv/bin/mineru

REVIEW_PROVIDER=ollama
REVIEW_MODEL=qwen2.5:7b
VISION_PROVIDER=codex
VISION_MODEL=default

TTS_PROVIDER=azure
AZURE_SPEECH_KEY=replace-with-secret
AZURE_SPEECH_REGION=eastasia
AZURE_SPEECH_VOICE=zh-CN-XiaoxiaoNeural
QWEN_TTS_URL=http://127.0.0.1:8020
```

要求：

- 数据库连接串和云服务密钥只放在服务器密钥文件或部署平台 Secrets 中。
- `DOTTY_DATA_DIR` 必须位于持久化磁盘。
- `CORS_ORIGINS` 填完整来源地址；`TRUSTED_HOSTS` 填域名，不使用任意通配符。
- 当前存储层首次启动会执行 `create_all()`；正式多版本发布前必须引入 Alembic。
- 升级已有 PostgreSQL 时，可依次执行 `backend/migrations/001_programmable_learning.sql` 和
  `backend/migrations/002_mistake_capture.sql`；执行前先备份数据库。

## 启动前检查

```bash
sudo -u dotty bash -lc '
  cd /opt/dotty-tutor/backend
  set -a
  . /etc/dotty-tutor/backend.env
  set +a
  ../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010
'
```

另开终端：

```bash
curl -fsS http://127.0.0.1:8010/api/health
curl -fsS http://127.0.0.1:8010/api/models
curl -fsS http://127.0.0.1:8010/api/ocr
curl -fsS http://127.0.0.1:8010/api/tts/status
```

## systemd

创建 `/etc/systemd/system/dotty-tutor-api.service`：

```ini
[Unit]
Description=Dotty Tutor FastAPI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dotty
Group=dotty
WorkingDirectory=/opt/dotty-tutor/backend
EnvironmentFile=/etc/dotty-tutor/backend.env
ExecStart=/opt/dotty-tutor/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010 --workers 1
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dotty-tutor-api
sudo systemctl status dotty-tutor-api
sudo journalctl -u dotty-tutor-api -f
```

生产环境不要添加 `--reload`。当前同步 OCR 和模型调用可能持续数分钟，反向代理超时需要覆盖
最长请求；完成任务队列改造后再缩短超时。

## 前端构建

```bash
sudo -u dotty bash -lc '
  cd /opt/dotty-tutor/frontend
  npm ci
  npm run build
'

sudo mkdir -p /var/www/dotty-tutor
sudo rsync -a --delete /opt/dotty-tutor/frontend/dist/ /var/www/dotty-tutor/
sudo chown -R www-data:www-data /var/www/dotty-tutor
```

## Nginx

创建 `/etc/nginx/sites-available/dotty-tutor`：

```nginx
server {
    listen 80;
    server_name tutor.example.com;

    root /var/www/dotty-tutor;
    index index.html;
    client_max_body_size 550m;

    location /api/ {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/dotty-tutor /etc/nginx/sites-enabled/dotty-tutor
sudo nginx -t
sudo systemctl reload nginx
```

使用 Certbot 或云负载均衡配置 HTTPS。防火墙只应开放 80/443；8010、11434、8020 和数据库
端口不能直接暴露到公网。

## Docker

Docker 部署包含：

| 文件 | 作用 |
| --- | --- |
| `compose.yaml` | 编排 PostgreSQL、FastAPI 和前端 Nginx |
| `Dockerfile.backend` | 构建单 worker、非 root 用户运行的 API 镜像 |
| `Dockerfile.frontend` | 使用 Node 构建前端，再复制到 Nginx 镜像 |
| `docker/nginx.conf` | 托管 SPA 并把 `/api/` 代理到 API 容器 |
| `.env.docker.example` | Docker 环境变量模板 |
| `.dockerignore` | 排除虚拟环境、模型、数据和构建产物 |

### 一键启动

需要 Docker Engine 24+ 和 Docker Compose v2：

```bash
git clone https://github.com/ningkaikok/dotty-tutor.git
cd dotty-tutor
cp .env.docker.example .env
```

编辑 `.env`，至少替换 `POSTGRES_PASSWORD`。由于 Compose 会把密码放入数据库 URL，建议
使用较长的字母、数字、下划线和短横线组合；不要使用需要 URL 编码的字符。

PostgreSQL 默认只绑定宿主机 `127.0.0.1:15432`，便于本机数据库工具访问且不会暴露到局域网。
连接用户、密码和数据库名分别来自 `POSTGRES_USER`、`POSTGRES_PASSWORD` 和 `POSTGRES_DB`。

```bash
docker compose config
docker compose up --build --detach
docker compose ps
```

打开 <http://localhost:8080>，通过 Nginx 同源访问前端和 `/api`。产品首页、教材学习和错题陪练
分别位于 `/`、`/textbooks`、`/mistakes`。仓库的 Nginx 配置已使用 `index.html` 作为 SPA 回退，
反向代理或 CDN 也必须保留该规则，否则直接刷新子路径会返回 404。默认服务拓扑：

```text
localhost:8080
  → web（Nginx + React）
       → api:8010（FastAPI）
            → db:5432（PostgreSQL）
```

API 和 PostgreSQL 不映射宿主端口，只在 Compose 内部网络中可见。Compose 会等待数据库和
API 健康后再启动依赖服务。

### 日志与状态

```bash
docker compose ps
docker compose logs --follow api
docker compose logs --follow db
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/api/health
```

### 停止与更新

```bash
# 停止服务，保留 PostgreSQL 和教材资源卷
docker compose down

# 拉取代码并重建
git pull --ff-only
docker compose up --build --detach
```

不要在没有备份时执行 `docker compose down --volumes`，它会删除数据库和教材资源卷。

### 数据卷

- `postgres_data`：PostgreSQL 数据目录。
- `dotty_data`：上传 PDF、Markdown、题图和其他教材资源。

查看实际卷名：

```bash
docker volume ls --filter name=dotty-tutor
```

生产环境建议把数据库替换为托管 PostgreSQL，并把教材资源层替换为对象存储；Compose 命名卷
主要用于单机内测。

### 连接宿主模型服务

基础 Compose 默认使用 Mock 生成和审校，确保没有 GPU 和模型权重也能启动。要连接运行在
Docker 宿主机上的 Ollama 或 Qwen3-TTS，在 `.env` 中修改：

```dotenv
MODEL_PROVIDER=ollama
MODEL_NAME=qwen2.5:3b
REVIEW_PROVIDER=ollama
REVIEW_MODEL=qwen2.5:7b
OLLAMA_BASE_URL=http://host.docker.internal:11434

TTS_PROVIDER=qwen
QWEN_TTS_URL=http://host.docker.internal:8020
```

Compose 已把 `host.docker.internal` 映射到宿主机。Ollama 和 Qwen3-TTS 必须监听 Docker
可访问的地址，并通过主机防火墙限制访问。基础 API 镜像不包含 MinerU、Qwen 权重或 Codex
CLI；这些重型运行时应部署为独立服务或通过专用镜像接入。

### 域名和 HTTPS

Docker 内的 Nginx 只提供 HTTP。公网部署时应在 Compose 前增加云负载均衡、Caddy、Traefik
或宿主 Nginx，用于 TLS 证书和域名转发，同时设置：

```dotenv
CORS_ORIGINS=https://tutor.example.com
TRUSTED_HOSTS=tutor.example.com
WEB_PORT=8080
```

只向公网开放外层代理的 443 端口，不直接暴露数据库、API 或模型服务。

## 发布验收

每次发布至少检查：

1. GitHub Actions 全部通过。
2. `/api/health` 返回正常且数据库可访问。
3. 上传一个小 PDF，验证暂停续传和首题生成。
4. 验证题图、OCR 产物、选择/判断/画线交互和 Help。
5. 验证 TTS 以及浏览器回退。
6. 确认 `needs_review` 题目没有直接发布给真实学生。
7. 检查日志中没有密钥、数据库连接串和内部堆栈泄漏。

## 备份与恢复

PostgreSQL 和文件资源必须同时备份：

```bash
pg_dump "$DATABASE_URL" --format=custom \
  --file=/srv/backup/dotty_tutor-$(date +%F).dump

tar -czf /srv/backup/dotty-data-$(date +%F).tar.gz \
  /srv/dotty-tutor/data
```

上线前应执行一次恢复演练，确认数据库记录引用的 PDF 和题图同时恢复。

## 更新

```bash
sudo -u dotty git -C /opt/dotty-tutor pull --ff-only
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install \
  -r /opt/dotty-tutor/backend/requirements.txt
sudo -u dotty bash -lc 'cd /opt/dotty-tutor/frontend && npm ci && npm run build'
sudo rsync -a --delete /opt/dotty-tutor/frontend/dist/ /var/www/dotty-tutor/
sudo systemctl restart dotty-tutor-api
sudo systemctl reload nginx
```

当前错题录入表可以使用 `backend/migrations/002_mistake_capture.sql` 幂等创建。引入 Alembic 后，
应在 API 重启前运行 `alembic upgrade head`，并为失败迁移准备回滚方案。

## GitHub CI

`.github/workflows/ci.yml` 在推送和 Pull Request 时执行：

- Python 3.12 后端测试；
- Node.js 20 前端构建；
- 后端 Docker 镜像构建。

生产自动部署应使用 GitHub Environments 和 Secrets，并要求 CI 通过后才能发布。当前工作流
只验证构建，不会自动连接或修改生产服务器。
