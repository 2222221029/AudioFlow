# ============================================================
# 镜像源策略：
#   GitHub Actions 构建（docker-image.yml）不传 build-args → 官方源
#   （海外 runner 最快；上一版误加阿里云/npmmirror 国内源导致 apt 一步
#    503s、pip 两步 291s+128s，总构建从 4.5 分钟暴涨到 17 分钟）。
#   飞牛 NAS 等国内环境本地构建 → 传入国内镜像源（见 docker-compose.yml
#   的 build.args / docker build --build-arg ...），避免官方源超时卡死。
# ============================================================

FROM node:22-alpine AS frontend-build

WORKDIR /app

ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}

COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS developer-agent-runtime

WORKDIR /opt/audioflow-developer-agent
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}
COPY developer-agent/package*.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --omit=dev --legacy-peer-deps --no-audit --no-fund

FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG AUDIOFLOW_UID=1000
ARG AUDIOFLOW_GID=1000
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ARG APT_MIRROR=

ENV APP_MODE=server \
    HOST=0.0.0.0 \
    PORT=8082 \
    DATA_DIR=/app/data \
    CONFIG_DIR=/app/config \
    DOWNLOAD_DIR=/app/downloads \
    LOG_DIR=/app/logs \
    AUDIOFLOW_LOG_LEVEL=INFO \
    AUDIOFLOW_PLATFORM_VERBOSE=0 \
    AUDIOFLOW_DOWNLOAD_VERBOSE=0 \
    PWA_ENABLED=true \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    TZ=Asia/Shanghai

WORKDIR /app

# apt 源：默认官方源（Actions 快）；国内构建传 APT_MIRROR 切换（如 mirrors.aliyun.com）
RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
            /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    fi \
    && apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends \
        ffmpeg \
        ca-certificates \
        tzdata \
        curl \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -g ${AUDIOFLOW_GID} audioflow \
    && useradd -u ${AUDIOFLOW_UID} -g audioflow -d /app -s /usr/sbin/nologin audioflow

COPY requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 600 --retries 20 -r requirements.txt

# The image is Linux-based, which is supported by the Harness runtime wheel.
# AudioFlow still defaults to its native runtime and selects Harness in the UI.
# 镜像源由 PIP_INDEX_URL build-arg 控制（默认官方源，国内构建传阿里云）
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --timeout 600 --retries 20 deepseek-harness-sdk

COPY --from=developer-agent-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=developer-agent-runtime /usr/local/bin/npm /usr/local/bin/npm
COPY --from=developer-agent-runtime /usr/local/bin/npx /usr/local/bin/npx
COPY --from=developer-agent-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=developer-agent-runtime /opt/audioflow-developer-agent /app/developer-agent

COPY --chown=audioflow:audioflow . /app
COPY --chown=audioflow:audioflow --from=frontend-build /app/dist /app/frontend/dist

RUN mkdir -p /app/data /app/config /app/downloads /app/logs /workspace \
    && chown -R audioflow:audioflow /app/data /app/config /app/downloads /app/logs /workspace

USER audioflow

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["python", "web_server.py"]
