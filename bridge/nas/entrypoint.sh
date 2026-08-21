#!/bin/sh
set -eu

ADB_TARGET="${ADB_TARGET:-redroid:5555}"
PACKAGE_NAME="${AUDIOFLOW_BRIDGE_PACKAGE:-com.ximalaya.ting.android}"
EXPECTED_VERSION="${AUDIOFLOW_BRIDGE_APP_VERSION:-9.4.52.3}"
REMOTE_FRIDA="/data/local/tmp/audioflow-frida-server"

ADB_HOST="${ADB_TARGET%:*}"
ADB_PORT="${ADB_TARGET##*:}"
ADB_IP="$(getent ahostsv4 "${ADB_HOST}" | sed -n '1{s/ .*//;p;}')"
if [ -n "${ADB_IP}" ]; then
    ADB_TARGET="${ADB_IP}:${ADB_PORT}"
fi

echo "等待 ReDroid ADB：${ADB_TARGET}"
until adb connect "${ADB_TARGET}" >/dev/null 2>&1; do
    sleep 2
done

adb -s "${ADB_TARGET}" wait-for-device
echo "等待 Android 完成启动"
until [ "$(adb -s "${ADB_TARGET}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = "1" ]; do
    sleep 2
done

echo "切换 ADB Root"
adb -s "${ADB_TARGET}" root >/dev/null 2>&1 || true
sleep 2
until adb connect "${ADB_TARGET}" >/dev/null 2>&1 \
    && adb -s "${ADB_TARGET}" shell id -u 2>/dev/null | tr -d '\r' | grep -qx '0'; do
    adb -s "${ADB_TARGET}" root >/dev/null 2>&1 || true
    sleep 2
done

APP_VERSION="$(adb -s "${ADB_TARGET}" shell dumpsys package "${PACKAGE_NAME}" 2>/dev/null \
    | sed -n 's/.*versionName=//p' | head -n 1 | tr -d '\r')"
if [ "${APP_VERSION}" != "${EXPECTED_VERSION}" ]; then
    echo "喜马拉雅版本不匹配：当前 ${APP_VERSION:-未安装}，需要 ${EXPECTED_VERSION}" >&2
    exit 10
fi

adb -s "${ADB_TARGET}" push /opt/audioflow-frida-server "${REMOTE_FRIDA}" >/dev/null
# ReDroid and the bridge share a private Compose network. Listen on the
# container interface so the bridge can reach Frida without publishing 27042
# on the NAS host.
adb -s "${ADB_TARGET}" shell "chmod 0755 '${REMOTE_FRIDA}'; killall audioflow-frida-server >/dev/null 2>&1 || true; nohup '${REMOTE_FRIDA}' --listen=0.0.0.0:27042 >/data/local/tmp/audioflow-frida.log 2>&1 </dev/null &" >/dev/null

python - <<'PY'
import os
import socket
import time

host, port = os.environ.get("AUDIOFLOW_BRIDGE_FRIDA_ADDRESS", "redroid:27042").rsplit(":", 1)
deadline = time.time() + 45
while time.time() < deadline:
    try:
        with socket.create_connection((host, int(port)), timeout=2):
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("Frida Server 未在 45 秒内就绪")
PY

echo "ReDroid 与 Frida 已就绪，启动 AudioFlow Bridge"
exec python -m bridge.server --config /app/bridge/config.nas.json
