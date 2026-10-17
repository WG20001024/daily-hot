# =============================================================
# 每日热点 —— gunicorn 配置（可选生产模式）
# 启用方式：环境变量 SERVER=gunicorn
# 注意：workers 必须保持 1，否则定时任务会重复抓取。
# =============================================================
import os

# 容器内默认没有 IPv6，绑定 0.0.0.0 最稳妥；
# 若宿主机开启了 IPv6，可设 BIND="[::]:5000"
bind = os.environ.get('BIND', '0.0.0.0:5000')
workers = 1                                   # 定时任务只能在单进程内运行
threads = int(os.environ.get('GUNICORN_THREADS', 8))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', 120))
graceful_timeout = 30
preload_app = True
accesslog = '-'
errorlog = '-'
capture_output = True


def post_fork(server, worker):
    """worker 启动后拉起 APScheduler（每日抓取 + Telegram/邮件推送）。"""
    try:
        from app import app as flask_app
        flask_app.start_scheduler()
        print('[gunicorn] 定时任务已启动', flush=True)
    except Exception as exc:  # pragma: no cover
        print('[gunicorn] 定时任务启动失败: %s' % exc, flush=True)
