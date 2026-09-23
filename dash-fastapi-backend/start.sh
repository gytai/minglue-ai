#!/usr/bin/env bash
#
# Dash-FastAPI 后端 - 开发环境启动脚本
#
# 用法:
#   ./start.sh
#
# 说明:
#   - 强制 APP_ENV=dev, 加载 .env.dev
#   - 通过 app.py 入口启动 uvicorn, 启用 hot reload (.env.dev 中 APP_RELOAD=true)
#   - PYTHONUNBUFFERED=1 让日志实时输出

set -euo pipefail

# 切到脚本所在目录, 保证 .env.dev / .env.prod 能被相对路径正确加载
cd "$(dirname "${BASH_SOURCE[0]}")"

export PYTHONUNBUFFERED=1
export APP_ENV=dev

echo ">>> Dash-FastAPI backend starting"
echo "    env    : ${APP_ENV}"
echo "    reload : enabled"
echo

exec python app.py
