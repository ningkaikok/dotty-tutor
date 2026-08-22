FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_PROJECT_ENVIRONMENT=/app/apps/api/.venv \
    PATH="/app/apps/api/.venv/bin:$PATH"

WORKDIR /app/apps/api

# poppler 提供 pdftoppm 页面渲染兜底；uv 提供与 CI/本地一致的精确锁安装。
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.12.5

COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=dotty:dotty apps/api /app/apps/api

RUN groupadd --system dotty \
    && useradd --system --gid dotty --home-dir /app --shell /usr/sbin/nologin dotty \
    && mkdir -p /data/uploads \
    && chown -R dotty:dotty /app /data

USER dotty

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=3)" || exit 1

# This image contains the API only. Run Ollama, MinerU and Qwen3-TTS as
# separate services and configure their URLs/commands through the environment.
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
EXPOSE 8010
