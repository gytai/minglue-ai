#!/usr/bin/env bash
#
# Dash-FastAPI 后端 - 生产环境启动脚本
#
# 用法:
#   ./start_pro.sh
#
# 可通过环境变量覆盖默认值:
#   APP_HOST        监听地址        默认 0.0.0.0
#   APP_PORT        监听端口        默认 9099
#   APP_WORKERS     worker 进程数   默认 1, 建议设为 CPU 核数
#   APP_ROOT_PATH   反向代理前缀    默认 /prod-api
#
# 示例: APP_WORKERS=4 ./start_pro.sh
#
# 说明:
#   - 强制 APP_ENV=prod, 加载 .env.prod (其中 APP_RELOAD=false)
#   - 直接调用 uvicorn 命令行以支持多 worker 部署
#   - 开启 --proxy-headers 以正确处理 nginx 等前置反代的 X-Forwarded-* 头

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

export PYTHONUNBUFFERED=1
export APP_ENV=prod

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-9099}"
WORKERS="${APP_WORKERS:-1}"
ROOT_PATH="${APP_ROOT_PATH:-/prod-api}"

echo ">>> Dash-FastAPI backend starting (production)"
echo "    env       : ${APP_ENV}"
echo "    host      : ${HOST}"
echo "    port      : ${PORT}"
echo "    workers   : ${WORKERS}"
echo "    root_path : ${ROOT_PATH}"
echo

exec uvicorn app:app \
  --host "${HOST}" \
  --port "${PORT}" \
  --workers "${WORKERS}" \
  --root-path "${ROOT_PATH}" \
  --proxy-headers
