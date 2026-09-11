FROM node:22-alpine AS frontend-build

WORKDIR /app

# 国内 npm 镜像加速：飞牛 NAS 等国内环境本地构建时避免长时间卡在官方源下载
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com

COPY frontend/package*.json ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS developer-agent-runtime

WORKDIR /opt/audioflow-developer-agent
ENV NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
COPY developer-agent/package*.json ./
RUN npm ci --omit=dev --legacy-peer-deps --no-audit --no-fund

FROM python:3.12-slim

ARG DEBIAN_FRONTEND=noninteractive
ARG AUDIOFLOW_UID=1000
ARG AUDIOFLOW_GID=1000

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
    # 国内 PyPI 镜像：pip install 默认走阿里云源。
    # 此前 deepseek-harness-sdk 步骤未指定镜像源，默认访问 pypi.org 在
    # 国内网络下超时重试（--timeout 600 --retries 20），曾导致构建卡数小时。
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    TZ=Asia/Shanghai

WORKDIR /app

# 换国内 apt 源（阿里云），加速 ffmpeg 等系统包安装
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
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
RUN pip install --timeout 600 --retries 20 -r requirements.txt

# The image is Linux-based, which is supported by the Harness runtime wheel.
# AudioFlow still defaults to its native runtime and selects Harness in the UI.
# 依赖 PIP_INDEX_URL 环境变量走阿里云源（此前漏配镜像源导致构建卡死）
RUN pip install --timeout 600 --retries 20 deepseek-harness-sdk

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
