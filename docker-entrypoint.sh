#!/bin/sh
# =============================================================
# 每日热点 —— 容器启动入口
#   1. 等待 MySQL 并自动建库 / 建表 / 执行增量迁移
#   2. 启动 Web 服务（默认 Flask 内置服务器；SERVER=gunicorn 时用 gunicorn）
# =============================================================
set -e

echo "[entrypoint] 每日热点 启动中... (TZ=${TZ:-未设置})"

# ---- 1. 数据库初始化（SKIP_DB_INIT=1 可跳过）----
if [ "${SKIP_DB_INIT}" != "1" ]; then
  python /app/init_db.py || {
    echo "[entrypoint] 数据库初始化失败，退出" >&2
    exit 1
  }
fi

# ---- 2. 启动 Web 服务 ----
if [ "${SERVER}" = "gunicorn" ]; then
  echo "[entrypoint] 使用 gunicorn 启动（workers=1，定时任务在 worker 内运行）"
  exec python -m gunicorn -c /app/gunicorn.conf.py app:app
else
  echo "[entrypoint] 使用 Flask 内置服务器启动"
  exec python /app/app.py "$@"
fi
