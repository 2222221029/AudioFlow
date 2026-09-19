# ============================================================
# 镜像源策略：
#   全部使用官方源（Dockerfile 默认 npmjs.org / pypi.org / deb.debian.org），
#   构建环境能访问外网（GitHub Actions、直连或代理）时速度最快。
#   如需国内镜像源可自行用 build-arg 覆盖（PIP_INDEX_URL/NPM_REGISTRY/APT_MIRROR）。
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

# apt 源：默认官方源；如需国内镜像可传 APT_MIRROR 覆盖（如 mirrors.aliyun.com）
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

COPY --chown=audioflow:audioflow . /app
COPY --chown=audioflow:audioflow --from=frontend-build /app/dist /app/frontend/dist

RUN mkdir -p /app/data /app/config /app/downloads /app/logs /workspace \
    && chown -R audioflow:audioflow /app/data /app/config /app/downloads /app/logs /workspace

# 规范化源码权限。COPY --chown 只改属主、不改 mode，而上面的 USER audioflow 意味着
# 运行期是非 root：任何 mode 过严的源文件（例如本地工具写入产生的 000/600）都会让
# import 直接抛 PermissionError，容器启动即失败。a+rX 只补读权限，目录补执行位。
RUN chmod -R a+rX /app

USER audioflow

EXPOSE 8082

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

CMD ["python", "web_server.py"]
