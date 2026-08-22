# React Router 8 requires Node 22.x in the container build.  Keep the
# development and production toolchains on the same supported major line.
FROM node:22-alpine AS build

# monorepo 构建形态：工作区清单与应用源码分层拷贝，最大化层缓存。
WORKDIR /app
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

# 第一层：工作区根清单（pnpm 版本由 packageManager 字段经 corepack 固定）。
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile

# 第二层：应用源码与专属清单。
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile --filter dotty-tutor-web
COPY apps/web/ apps/web/

# dist 输出在 /app/apps/web/dist，nginx 阶段按同路径复制。
RUN pnpm --filter dotty-tutor-web run build

FROM nginx:1.27-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
