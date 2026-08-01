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
DATABASE_URL=postgresql+psycopg://dotty_app:password@db.example.com:5432/dotty_tutor?sslmode=require
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

项目根目录的 `Dockerfile.backend` 只构建 API，不包含 Ollama、MinerU 或 Qwen3-TTS：

```bash
docker build -f Dockerfile.backend -t dotty-tutor-api .
docker run --rm -p 8010:8010 \
  --env-file /path/to/backend.env \
  -v /srv/dotty-tutor/data:/data \
  dotty-tutor-api
```

容器内的 `DATABASE_URL` 不能使用 `127.0.0.1` 指向宿主数据库，应使用 Docker 网络服务名或
云数据库地址。

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

引入 Alembic 后，应在 API 重启前运行 `alembic upgrade head`，并为失败迁移准备回滚方案。

## GitHub CI

`.github/workflows/ci.yml` 在推送和 Pull Request 时执行：

- Python 3.12 后端测试；
- Node.js 20 前端构建；
- 后端 Docker 镜像构建。

生产自动部署应使用 GitHub Environments 和 Secrets，并要求 CI 通过后才能发布。当前工作流
只验证构建，不会自动连接或修改生产服务器。
