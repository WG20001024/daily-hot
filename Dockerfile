# =============================================================
# 每日热点 —— Docker 镜像
# 构建： docker build -t daily-hot:latest .
# 运行： docker compose up -d   （推荐，自动带 MySQL 8）
# =============================================================
FROM python:3.11-slim

# 可用 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple 加速
ARG PIP_INDEX_URL=""

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    PORT=5000 \
    HOST=::

WORKDIR /app

# tzdata：保证定时任务按东八区执行；curl：便于容器内排查
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime

# 依赖单独一层，改代码时不必重装依赖
COPY requirements.txt ./
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    else \
        pip install --no-cache-dir -r requirements.txt \
        || pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt; \
    fi

COPY . ./

# 非 root 运行
RUN useradd -m -u 1000 -s /bin/sh appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:'+__import__('os').environ.get('PORT','5000')+'/healthz', timeout=4)" || exit 1

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
