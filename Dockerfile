# Jibsz Tunneling / J1BTNL — Railway Docker Edition (V4.9)
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV TZ=Asia/Jakarta
ENV INSTALL_DIR=/opt/jibsz-tunnel
ENV LOG_DIR=/var/log/jibsz
ENV RAILWAY_MODE=1

ARG XRAY_VERSION=v26.11.9

RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx \
    python3 \
    python3-pip \
    python3-venv \
    sqlite3 \
    dropbear \
    openssh-server \
    curl \
    unzip \
    ca-certificates \
    procps \
    iproute2 \
    openssl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# Install Xray-core (default v26.11.9, fallback v1.8.24)
RUN mkdir -p /usr/local/bin /usr/local/etc/xray /usr/local/share/xray /tmp/xray-dl \
    && (curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip" -o /tmp/xray.zip \
        || curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/v1.8.24/Xray-linux-64.zip" -o /tmp/xray.zip) \
    && unzip -qo /tmp/xray.zip -d /tmp/xray-dl \
    && install -m 755 /tmp/xray-dl/xray /usr/local/bin/xray \
    && cp -f /tmp/xray-dl/*.dat /usr/local/share/xray/ 2>/dev/null || true \
    && cp -f /tmp/xray-dl/*.dat /usr/local/bin/ 2>/dev/null || true \
    && rm -rf /tmp/xray.zip /tmp/xray-dl

WORKDIR /opt/jibsz-tunnel

COPY webpanel/requirements.txt /opt/jibsz-tunnel/webpanel/requirements.txt
RUN python3 -m venv /opt/jibsz-tunnel/venv \
    && /opt/jibsz-tunnel/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/jibsz-tunnel/venv/bin/pip install --no-cache-dir -r /opt/jibsz-tunnel/webpanel/requirements.txt

COPY backend /opt/jibsz-tunnel/backend
COPY cli /opt/jibsz-tunnel/cli
COPY webpanel /opt/jibsz-tunnel/webpanel
COPY docker/nginx_railway.conf.template /opt/jibsz-tunnel/docker/nginx_railway.conf.template
COPY docker/entrypoint.sh /opt/jibsz-tunnel/entrypoint.sh

RUN chmod +x /opt/jibsz-tunnel/entrypoint.sh \
    /opt/jibsz-tunnel/backend/ssh_ws_proxy.py \
    /opt/jibsz-tunnel/cli/*.sh \
    && mkdir -p /etc/jibsz /var/log/jibsz /root/jibsz-backups /var/run/sshd \
    && rm -f /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*

EXPOSE 8080

ENTRYPOINT ["/opt/jibsz-tunnel/entrypoint.sh"]
