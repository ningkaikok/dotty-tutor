# React Router 8 requires Node 22.22+ in the container build.  Keep the
# development and production toolchains on the same supported major line.
FROM node:22-alpine AS build

WORKDIR /app/frontend
# monorepo：先拷工作区清单再装依赖，利用层缓存
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate \
    && pnpm install --frozen-lockfile

COPY apps/web/ ./
RUN pnpm run build

FROM nginx:1.27-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/apps/web/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
