#!/usr/bin/env bash

set -euo pipefail

NODE_BIN="${NODE_BIN:-node}"

if ! command -v "$NODE_BIN" >/dev/null 2>&1; then
  echo "未找到 Node.js（当前命令：$NODE_BIN）。请安装 Node.js 20.19+（20.x）或 22.12+，再重新运行。" >&2
  exit 1
fi

raw_version="$($NODE_BIN --version 2>/dev/null || true)"
if [[ ! "$raw_version" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "无法读取 Node.js 版本（$raw_version）。请确认 node --version 返回形如 v20.19.0 的版本号。" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
patch="${BASH_REMATCH[3]}"

supported=0
if (( major == 20 && minor >= 19 )); then
  supported=1
elif (( major == 22 && minor >= 12 )); then
  supported=1
elif (( major >= 23 )); then
  supported=1
fi

if (( supported == 0 )); then
  echo "当前 Node.js 为 v${major}.${minor}.${patch}，不受本项目支持。" >&2
  echo "Vite 8 需要 Node.js 20.19+（20.x）或 22.12+；请使用 nvm、fnm 或 asdf 切换后再运行：" >&2
  echo "  nvm install 20.19.0 && nvm use 20.19.0" >&2
  exit 1
fi

echo "Node.js v${major}.${minor}.${patch} 已通过运行基线检查。"
