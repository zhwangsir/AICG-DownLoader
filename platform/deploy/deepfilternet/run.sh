#!/usr/bin/env bash
# DeepFilterNet3 HTTP 服务启动脚本 (macOS, launchd 守护)
# 部署位置: studio01 (192.168.71.109): ~/deploys/deepfilternet/
# 端口: 8301
# 依赖: Python 3.9+ (系统自带) + deep-filter 二进制 (同目录)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${DEPLOY_DIR}/deep-filter"
SERVE="${DEPLOY_DIR}/serve_api.py"
PORT="${DF_PORT:-8301}"
HOST="${DF_HOST:-0.0.0.0}"

# 后台启动 + 日志
LOG_FILE="${DEPLOY_DIR}/serve.log"
PID_FILE="${DEPLOY_DIR}/serve.pid"

if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  echo "service already running (pid=$(cat "${PID_FILE}"))"
  exit 0
fi

mkdir -p /tmp/deepfilternet-io
export DF_BIN="${BIN}"
export DF_PORT="${PORT}"
export DF_HOST="${HOST}"

nohup python3 "${SERVE}" >"${LOG_FILE}" 2>&1 &
echo $! >"${PID_FILE}"
sleep 1
echo "started (pid=$(cat "${PID_FILE}")), listening on ${HOST}:${PORT}"
echo "logs: tail -f ${LOG_FILE}"
