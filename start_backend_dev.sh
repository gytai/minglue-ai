#!/usr/bin/env bash
#
# 明略 AI - 后端开发环境启动脚本
#
# 用法:
#   ./start_backend_dev.sh
#
# 前置条件:
#   - Python 3.10+ 已安装 (用于创建 venv)
#   - 已配置 dash-fastapi-backend/.env.dev
#   - 数据库 (MySQL/PostgreSQL) 与 Redis 已启动
#
# 自动完成的步骤:
#   1. 在仓库根目录创建 .venv (若不存在)
#   2. 按需安装/更新前后端依赖 (基于 requirements.txt 的 mtime 判幂等)
#   3. 切换到 dash-fastapi-backend/ 后以 dev 模式启动 (hot reload)
#
# 数据库分支 (默认 MySQL):
#   REQUIREMENTS_FILE=requirements-pg.txt ./start_backend_dev.sh
#
# 覆盖端口/监听:
#   APP_HOST=127.0.0.1 APP_PORT=9099 ./start_backend_dev.sh
#
# 强制重装依赖:
#   rm -rf .venv/.deps_installed && ./start_backend_dev.sh

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
# 3. 启动后端 (dev)
# ---------------------------------------------------------------------------
cd "${ROOT_DIR}/dash-fastapi-backend"

export PYTHONUNBUFFERED=1
export APP_ENV=dev

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-9099}"

echo ">>> Minglue backend starting (development)"
echo "    env    : ${APP_ENV}"
echo "    host   : ${HOST}"
echo "    port   : ${PORT}"
echo "    reload : enabled (uvicorn)"
echo "    venv   : ${VENV_DIR}"
echo "    url    : http://127.0.0.1:${PORT}/docs"
echo

exec env APP_HOST="${HOST}" APP_PORT="${PORT}" "${VENV_PY}" app.py