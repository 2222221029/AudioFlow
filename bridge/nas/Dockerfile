FROM python:3.11-slim

ARG FRIDA_VERSION=16.7.19

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fL "https://dl.google.com/android/repository/platform-tools-latest-linux.zip" -o /tmp/platform-tools.zip \
    && unzip -q /tmp/platform-tools.zip -d /opt \
    && ln -s /opt/platform-tools/adb /usr/local/bin/adb \
    && rm /tmp/platform-tools.zip \
    && pip install --no-cache-dir "frida==${FRIDA_VERSION}" \
    && curl -fL "https://github.com/frida/frida/releases/download/${FRIDA_VERSION}/frida-server-${FRIDA_VERSION}-android-x86_64.xz" \
       | xz -d > /opt/audioflow-frida-server \
    && chmod 0755 /opt/audioflow-frida-server

WORKDIR /app
COPY bridge /app/bridge

RUN chmod 0755 /app/bridge/nas/entrypoint.sh

EXPOSE 17891
ENTRYPOINT ["/app/bridge/nas/entrypoint.sh"]
