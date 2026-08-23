#!/bin/bash
# 任务中心 hydrate 500 复现与诊断脚本（P2 根因：vite dev 代理错误路径，非后端 bug）
#
# 背景：浏览器实测曾报 [task-center] hydrate failed HTTPError 500（GET /api/v1/projects/<id>/tasks），
# 而后端日志零错误。根因：vite dev 代理的 node-http-proxy 无 error handler，后端
# 重启窗口/不可达时对浏览器回「裸 500（空响应体）」，与真实服务端异常无法区分。
#
# 用法:
#   bash scripts/repro_task_center_500.sh              # 诊断模式：不改动任何服务
#   bash scripts/repro_task_center_500.sh --reproduce  # 复现模式：停后端→验证500→起临时vite→
#                                                      #   验证503修复→恢复后端→清理
#
# 退出码：0 = 环境健康或复现符合预期；1 = 诊断发现异常
set -u

BACKEND="http://127.0.0.1:8780"
PROJECT="${DASHBOX_PROJECT_ID:-01M0453H04HY5KVVHHZHC3M1QP}"
VITE="${DASHBOX_VITE_URL:-http://127.0.0.1:5180}"
TASKS_PATH="/api/v1/projects/$PROJECT/tasks"
cd "$(dirname "$0")/.."

probe() { # $1=url $2=timeout
  curl -s -o /tmp/repro_p2_body.json -w "%{http_code}" --max-time "${2:-8}" "$1" 2>/dev/null
}

echo "== 诊断：任务中心 /tasks 链路 =="
direct=$(probe "$BACKEND$TASKS_PATH")
echo "直连后端   ($BACKEND): HTTP $direct"
via_proxy=$(probe "$VITE$TASKS_PATH" 10)
echo "经 vite 代理($VITE): HTTP $via_proxy"

if [ "$direct" = "200" ] && [ "$via_proxy" = "200" ]; then
  echo "结论：链路健康（后端与代理均 200）。"
else
  echo "异常：direct=$direct via_proxy=$via_proxy；若 direct=200 而 via_proxy=500/502，"
  echo "      说明 500 来自 vite 代理（后端不可达窗口），不是后端代码 bug。"
  exit 1
fi

if [ "${1:-}" != "--reproduce" ]; then
  echo "（--reproduce 可完整复现 500 机制并验证 503 修复；当前仅诊断）"
  exit 0
fi

# ---- 复现模式 ----
echo
echo "== 复现：后端不可达时代理的行为 =="
backend_pid=$(lsof -ti :8780 | head -1)
if [ -z "$backend_pid" ]; then
  echo "后端本就未运行。"
else
  echo "停止后端 (pid=$backend_pid) ..."
  lsof -ti :8780 | xargs kill -9 2>/dev/null
  sleep 1
fi

code=$(probe "$VITE$TASKS_PATH" 8)
echo "后端停止后经代理请求: HTTP $code  响应体: $(head -c 200 /tmp/repro_p2_body.json 2>/dev/null)"
if [ "$code" = "500" ]; then
  echo ">>> 复现成功：这就是浏览器实测看到的 500（vite 代理默认错误响应，请求从未到达后端）。"
elif [ "$code" = "503" ]; then
  echo ">>> 已修复：vite.config.ts 的 proxy error handler 生效（503 + backend_unavailable JSON）。"
else
  echo ">>> 未预期状态码 $code（检查 vite 是否在跑/是否加载了新配置——配置改动需重启 vite）。"
fi

echo
echo "== 恢复后端 =="
( ST_EDITION=ce RELEASE_NOTIFICATIONS_ENABLED=false MEDIA_RELAY_PROVIDER=local_http \
    COGNEE_EMBEDDING_DIM=2560 nohup uv run novelvideo api --port 8780 \
    >/tmp/repro_p2_backend.log 2>&1 & )
for i in $(seq 1 60); do
  [ "$(probe "$BACKEND/healthz" 2)" = "200" ] && break
  sleep 1
done
restored=$(probe "$BACKEND$TASKS_PATH")
echo "后端恢复后直连: HTTP $restored"
[ "$restored" = "200" ] || { echo "后端恢复失败，查看 /tmp/repro_p2_backend.log"; exit 1; }
echo "完成。"
