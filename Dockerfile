FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/usr/local/bin:${PATH}"
ENV INSTALL_DIR="/opt/jibsz-tunnel"
ENV CONFIG_DIR="/etc/jibsz"
ENV LOG_DIR="/var/log/jibsz"

# Install dependensi sistem, Python, Nginx, Dropbear, OpenSSH, Supervisor, Cron
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget unzip ca-certificates gnupg \
    python3 python3-pip sqlite3 \
    nginx dropbear openssh-server \
    gettext-base procps cron supervisor \
    && rm -rf /var/lib/apt/lists/*

# Install Xray-core terbaru
RUN bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install \
    && mkdir -p /usr/local/etc/xray /var/log/jibsz /etc/jibsz /root/jibsz-backups /run/sshd

# Install Cloudflared (Cloudflare Tunnel) terbaru
RUN curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    -o /usr/local/bin/cloudflared \
    && chmod +x /usr/local/bin/cloudflared

WORKDIR /app
COPY . /app

# Symlink /opt/jibsz-tunnel ke /app agar path default tetap sinkron
RUN ln -sfn /app /opt/jibsz-tunnel

# Install library Python untuk FastAPI Web Panel
RUN pip3 install --no-cache-dir -r /app/webpanel/requirements.txt

# Pasang shim systemctl agar perintah restart/status dari Web Panel berjalan via Supervisor
RUN cp /app/systemctl_shim.sh /usr/local/bin/systemctl \
    && chmod +x /usr/local/bin/systemctl /app/start.sh /app/run_cloudflared.sh \
    && chmod +x /app/cli/*.sh /app/backend/*.sh /app/setup.sh 2>/dev/null || true

# Konfigurasi Supervisor
RUN cp /app/supervisord_jibsz.conf /etc/supervisor/conf.d/jibsz.conf

# Hapus site default nginx lama agar tidak bentrok port 80
RUN rm -f /etc/nginx/sites-enabled/default /etc/nginx/conf.d/default.conf

EXPOSE 8080

CMD ["/app/start.sh"]
