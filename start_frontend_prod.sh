#!/usr/bin/env bash
#
# 明略 AI - 前端生产环境启动脚本
#
# 用法:
#   ./start_frontend_prod.sh
#
# 前置条件:
#   - Python 3.10+ 已安装 (用于创建 venv)
#   - 已配置 dash-fastapi-frontend/.env.prod
#   - 后端已启动并监听在 .env.prod APP_BASE_URL 指向的地址
#
# 自动完成的步骤:
#   1. 在仓库根目录创建 .venv (若不存在)
#   2. 按需安装/更新前后端依赖
#   3. 切换到 dash-fastapi-frontend/ 后以 prod 模式启动 waitress
#
# 可通过环境变量覆盖默认值:
#   APP_HOST    监听地址   默认 0.0.0.0
#   APP_PORT    监听端口   默认 8088
#
# 示例:
#   APP_PORT=9000 ./start_frontend_prod.sh
#
# systemd 集成 (推荐生产使用, 不要直接 nohup 跑):
#   [Service]
#   WorkingDirectory=/opt/minglue-ai
#   ExecStart=/opt/minglue-ai/start_frontend_prod.sh
#   EnvironmentFile=/etc/minglue/frontend.env
#
# 强制重装依赖:
#   rm -rf .venv/.deps_installed && ./start_frontend_prod.sh

set -eo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
VENV_PY="${VENV_DIR}/bin/python"
MARKER="${VENV_DIR}/.deps_installed"
# 前后端共用同一个 requirements.txt (默认 MySQL; PG 用 REQUIREMENTS_FILE=requirements-pg.txt 切换)
# 显式 fallback 避免 bash 3.2 (macOS 默认) 在 set -u 下对嵌套 ${VAR:-...} 的边界 case
if [ -z "${REQUIREMENTS_FILE:-}" ]; then
    REQUIREMENTS_FILE="${ROOT_DIR}/requirements.txt"
fi
REQ_FILE="${REQUIREMENTS_FILE}"

# ---------------------------------------------------------------------------
# 1. 创建虚拟环境
# ---------------------------------------------------------------------------
if [ ! -x "${VENV_PY}" ]; then
    echo ">>> 创建虚拟环境: ${VENV_DIR}"
    if ! python3 -m venv "${VENV_DIR}" >/dev/null 2>&1; then
        echo "ERROR: 创建 venv 失败, 请确认 python3 版本 >= 3.10" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# 2. 按需安装/更新依赖
# ---------------------------------------------------------------------------
stale_marker() {
    [ ! -f "${MARKER}" ] && return 0
    [ "${REQ_FILE}" -nt "${MARKER}" ] && return 0
    return 1
}

if stale_marker; then
    echo ">>> 安装依赖 (venv: ${VENV_DIR})"
    echo "    requirements: ${REQ_FILE}"
    "${VENV_PY}" -m pip install --upgrade pip wheel setuptools >/dev/null
    "${VENV_PY}" -m pip install -r "${REQ_FILE}"
    mkdir -p "${VENV_DIR}"
    touch "${MARKER}"
fi

# ---------------------------------------------------------------------------
# 3. 启动前端 (prod, waitress)
# ---------------------------------------------------------------------------
cd "${ROOT_DIR}/dash-fastapi-frontend"

export PYTHONUNBUFFERED=1
export APP_ENV=prod

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8088}"

echo ">>> Minglue frontend starting (production)"
echo "    env    : ${APP_ENV}"
echo "    host   : ${HOST}"
echo "    port   : ${PORT}"
echo "    venv   : ${VENV_DIR}"
echo "    url    : http://${HOST}:${PORT}"
echo

exec env APP_HOST="${HOST}" APP_PORT="${PORT}" "${VENV_PY}" wsgi.py