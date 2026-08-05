FROM python:3.12-slim

WORKDIR /app

# ---- System dependencies ----
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    libreoffice-impress \
    libreoffice-writer \
    fonts-noto-cjk \
    curl \
    gnupg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ---- Node.js (for pptx skill's PptxGenJS) ----
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm config set registry https://registry.npmmirror.com \
    && npm install -g pptxgenjs

# ---- Python dependencies (build context = ./backend) ----
# 从 pyproject 安装运行依赖 + sandbox extra（opensandbox SDK，SANDBOX_ENABLED=true 需要）。
COPY pyproject.toml ./
COPY app/ ./app/
# gunicorn 仅容器内用于多 worker 起服务（start.sh 本地直跑用 uvicorn），故不入 pyproject。
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple ".[sandbox]" gunicorn

# ---- 可编辑 PPT（dashi-ppt skill）Node 依赖 + Chromium ----
# 该 skill 在 backend 容器内直接 `node <project>/node_modules/.bin/tsx ...` 渲染，
# 并用 playwright headless-shell 导出 pptx。必须在镜像里预装项目本地依赖和浏览器，
# 否则运行时报 "Cannot find module .../node_modules/..." 或 "Chrome executable not found"。
# 浏览器下载走 npmmirror 镜像（国内直连 playwright CDN 常超时）。
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
ENV PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
RUN cd app/skills/dashi-ppt/project \
    && npm ci \
    && node node_modules/playwright-core/cli.js install-deps chromium \
    && node node_modules/playwright-core/cli.js install chromium chromium-headless-shell

# ---- Application entrypoint ----
COPY server.py .

EXPOSE 8046

# ---- Runtime settings (overridable via env) ----
ENV HOST="0.0.0.0"
ENV PORT="8046"
ENV WORKERS=${WORKERS:-0}
ENV TIMEOUT="600"
ENV GRACEFUL_TIMEOUT="30"
ENV KEEP_ALIVE="75"
ENV WORKER_CONNECTIONS="1000"
ENV BACKLOG="2048"
ENV MAX_REQUESTS="10000"
ENV MAX_REQUESTS_JITTER="1000"

# ---- Health check ----
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8046/health')" || exit 1

CMD ["sh", "-c", "gunicorn server:app \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind ${HOST}:${PORT} \
    --workers ${WORKERS} \
    --timeout ${TIMEOUT} \
    --graceful-timeout ${GRACEFUL_TIMEOUT} \
    --keep-alive ${KEEP_ALIVE} \
    --worker-connections ${WORKER_CONNECTIONS} \
    --backlog ${BACKLOG} \
    --max-requests ${MAX_REQUESTS} \
    --max-requests-jitter ${MAX_REQUESTS_JITTER} \
    --access-logfile - \
    --error-logfile -"]
