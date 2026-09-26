#!/bin/bash
# ============================================================
# 部署最新的 AudioFlow 镜像（走国内 ghcr 代理）
#
# 背景：ghcr.io 的 blob 会 307 跳转到 pkg-containers.githubusercontent.com，
# 该域名在国内不可达，直接拉取会永远卡在 163MB 的大层上。本脚本使用
# docker-compose.image.yml 里配置的国内代理（实测 21MB/s）。
#
# 用法：
#   bash scripts/deploy-latest-image.sh
#
# 会自动完成：读取旧容器的 Cookie 密钥 → 拉新镜像 → 重建容器 → 健康检查
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.image.yml"
CONTAINER_NAME="audioflow"
HEALTH_URL="http://127.0.0.1:8082/health"

echo "=== 1/4 读取旧容器的 AUDIOFLOW_COOKIE_SECRET ==="
# 这个密钥必须与旧容器一致：cookies.json 里的凭证是用它加密的，
# 换掉之后所有已保存的登录态都解不开，只能重新扫码。
SECRET="$(docker inspect "$CONTAINER_NAME" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
  | sed -n 's/^AUDIOFLOW_COOKIE_SECRET=//p' | head -1 || true)"

if [ -z "${SECRET}" ]; then
  if [ -n "${AUDIOFLOW_COOKIE_SECRET:-}" ]; then
    SECRET="${AUDIOFLOW_COOKIE_SECRET}"
    echo "   旧容器读取失败，改用当前环境变量（长度 ${#SECRET}）"
  else
    echo "   ❌ 未能取到密钥。请先手动指定后重跑："
    echo "      export AUDIOFLOW_COOKIE_SECRET='你的密钥'"
    echo "      bash scripts/deploy-latest-image.sh"
    exit 1
  fi
else
  echo "   ✅ 已取到（长度 ${#SECRET}，未回显内容）"
fi
export AUDIOFLOW_COOKIE_SECRET="${SECRET}"

echo
echo "=== 2/4 拉取镜像（国内代理） ==="
docker compose -f "$COMPOSE_FILE" pull

echo
echo "=== 3/4 重建容器 ==="
docker compose -f "$COMPOSE_FILE" up -d

echo
echo "=== 4/4 等待并检查健康状态 ==="
for i in $(seq 1 20); do
  sleep 3
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then
    echo "   ✅ 服务已就绪（$HEALTH_URL → 200），用时约 $((i * 3)) 秒"
    break
  fi
  echo "   ... 第 $i 次探测：$code"
  if [ "$i" = "20" ]; then
    echo "   ⚠️ 60 秒内未就绪，下面是最近日志："
    docker compose -f "$COMPOSE_FILE" logs --tail 40
    exit 1
  fi
done

echo
echo "=== 当前容器 ==="
docker compose -f "$COMPOSE_FILE" ps
echo
echo "完成。浏览器打开 http://<NAS_IP>:8082 即可。"
