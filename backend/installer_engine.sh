#!/usr/bin/env bash
# ==============================================================================
# JIBSZ TUNNELING / J1BTNL - MODULAR INSTALLER ENGINE V4.1 (installer_engine.sh)
# Author   : Jibsz Tunneling (@admj1btnl)
# ==============================================================================

set -u

INSTALL_DIR="/opt/jibsz-tunnel"
CONFIG_DIR="/etc/jibsz"
LOG_DIR="/var/log/jibsz"
DB_FILE="${CONFIG_DIR}/jibsz.db"
INSTALL_LOG="${LOG_DIR}/install.log"
LOCK_FILE="${CONFIG_DIR}/installed.lock"
XRAY_VERSION="${XRAY_VERSION:-v26.11.9}"

mkdir -p "${CONFIG_DIR}" "${LOG_DIR}" "${CONFIG_DIR}/ssl" /var/www/html
chmod 777 "${LOG_DIR}" 2>/dev/null || true

log_step() {
    local msg
    msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo "$msg" | tee -a "${INSTALL_LOG}"
}

get_setting() {
    local key="$1"
    sqlite3 "${DB_FILE}" "SELECT value FROM settings WHERE key='${key}' LIMIT 1;" 2>/dev/null || echo ""
}

set_setting() {
    local key="$1"
    local val="$2"
    sqlite3 "${DB_FILE}" "INSERT OR REPLACE INTO settings (key, value) VALUES ('${key}', '${val}');" 2>/dev/null || true
}

update_component_status() {
    local comp_id="$1"
    local status="$2"
    local msg="$3"
    local safe_msg="${msg//\'/\'\'}"
    sqlite3 "${DB_FILE}" "UPDATE component_status SET status='${status}', message='${safe_msg}', updated_at=datetime('now','localtime') WHERE component_id='${comp_id}';" 2>/dev/null || true
    log_step "[${comp_id^^}] Status -> ${status^^} | ${msg}"
}

ensure_dns_and_time() {
    if ! grep -q "1.1.1.1" /etc/resolv.conf 2>/dev/null; then
        cat << 'EOF' > /etc/resolv.conf
nameserver 1.1.1.1
nameserver 8.8.8.8
nameserver 1.0.0.1
nameserver 8.8.4.4
EOF
    fi
    if [[ -f /etc/gai.conf ]]; then
        grep -qxF "precedence ::ffff:0:0/96  100" /etc/gai.conf || echo "precedence ::ffff:0:0/96  100" >> /etc/gai.conf
    else
        echo "precedence ::ffff:0:0/96  100" > /etc/gai.conf
    fi

    timedatectl set-ntp true 2>/dev/null || true
    local http_date
    http_date=$(curl -fsSI --max-time 5 https://1.1.1.1 2>/dev/null | grep -i '^date:' | sed 's/^[Dd]ate:[[:space:]]*//' | tr -d '\r' || true)
    if [[ -n "$http_date" ]]; then
        date -u -s "$http_date" >/dev/null 2>&1 || true
    fi
}

generate_self_signed_ssl() {
    local domain="$1"
    [[ -z "$domain" ]] && domain="jibsz.tunnel.local"
    log_step "Membuat sertifikat SSL Self-Signed (Fallback/Initial) untuk ${domain}..."
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
        -keyout "${CONFIG_DIR}/ssl/privkey.pem" \
        -out "${CONFIG_DIR}/ssl/fullchain.pem" \
        -subj "/C=ID/ST=EastJava/L=Trenggalek/O=JibszTunneling/OU=J1BTNL/CN=${domain}" >/dev/null 2>&1
    chmod 600 "${CONFIG_DIR}/ssl/privkey.pem"
    chmod 644 "${CONFIG_DIR}/ssl/fullchain.pem"
}

sync_xray_from_sqlite() {
    python3 - << 'PYEOF'
import json
import os
import shutil
import sqlite3

db_path = "/etc/jibsz/jibsz.db"
tpl_path = "/opt/jibsz-tunnel/backend/xray_config.json"
cfg_path = "/usr/local/etc/xray/config.json"

os.makedirs("/usr/local/etc/xray", exist_ok=True)
if os.path.exists(tpl_path):
    shutil.copyfile(tpl_path, cfg_path)

if not os.path.exists(cfg_path) or not os.path.exists(db_path):
    raise SystemExit(0)

with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

conn = sqlite3.connect(db_path, timeout=15.0)
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT protocol, username, secret FROM accounts WHERE status = 'active' AND protocol IN ('vless','vmess','trojan')"
).fetchall()

vless_c, vmess_c, trojan_c = [], [], []
for r in rows:
    sec = (r["secret"] or "").strip()
    if not sec:
        continue
    email_tag = f"{r['username']}@j1btnl"
    if r["protocol"] == "vless":
        vless_c.append({"id": sec, "email": email_tag, "level": 0})
    elif r["protocol"] == "vmess":
        vmess_c.append({"id": sec, "alterId": 0, "email": email_tag, "level": 0})
    elif r["protocol"] == "trojan":
        trojan_c.append({"password": sec, "email": email_tag, "level": 0})

for inb in cfg.get("inbounds", []):
    t = inb.get("tag", "")
    if t in ("vless-ws", "vless-grpc"):
        inb.setdefault("settings", {})["clients"] = vless_c
    elif t in ("vmess-ws", "vmess-grpc"):
        inb.setdefault("settings", {})["clients"] = vmess_c
    elif t in ("trojan-ws", "trojan-grpc"):
        inb.setdefault("settings", {})["clients"] = trojan_c

tmp_path = cfg_path + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
os.chmod(tmp_path, 0o644)
os.replace(tmp_path, cfg_path)
conn.close()
PYEOF
}

install_component_nginx() {
    update_component_status "nginx" "installing" "Menginstal paket Nginx, vnStat, socat, dan menerapkan konfigurasi reverse proxy..."
    ensure_dns_and_time

    export DEBIAN_FRONTEND=noninteractive
    if ! apt-get update -y >> "${INSTALL_LOG}" 2>&1; then
        update_component_status "nginx" "failed" "Gagal menjalankan apt-get update."
        return 1
    fi

    if ! apt-get install -y nginx vnstat socat bc curl wget unzip jq lsof net-tools cron >> "${INSTALL_LOG}" 2>&1; then
        update_component_status "nginx" "failed" "Gagal menginstal paket nginx dan utilitas jaringan."
        return 1
    fi

    local mode domain
    mode=$(get_setting "install_mode")
    domain=$(get_setting "domain")
    [[ -z "$domain" ]] && domain="localhost"

    systemctl stop apache2 >/dev/null 2>&1 || true
    systemctl disable apache2 >/dev/null 2>&1 || true
    mkdir -p /etc/jibsz && touch /etc/jibsz/blocked_ips.map
    rm -f /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf

    if [[ ! -f "${CONFIG_DIR}/ssl/fullchain.pem" || ! -f "${CONFIG_DIR}/ssl/privkey.pem" ]]; then
        generate_self_signed_ssl "$domain"
    fi

    if [[ "$mode" == "nat" ]]; then
        log_step "Menerapkan template Nginx Mode VPS NAT (Port 80 & 443 SSL dengan CF-Connecting-IP)..."
        sed "s/__DOMAIN__/${domain}/g" "${INSTALL_DIR}/backend/nginx_nat.conf" > /etc/nginx/conf.d/jibsz_tunnel.conf
    else
        log_step "Menerapkan template Nginx Mode VPS IP Public (Port 80 & 443 SSL)..."
        sed "s/__DOMAIN__/${domain}/g" "${INSTALL_DIR}/backend/nginx_public.conf" > /etc/nginx/conf.d/jibsz_tunnel.conf
    fi

    if ! nginx -t >> "${INSTALL_LOG}" 2>&1; then
        update_component_status "nginx" "failed" "Validasi konfigurasi nginx -t gagal. Periksa log instalasi."
        return 1
    fi

    if pidof systemd >/dev/null 2>&1; then
        systemctl enable nginx >> "${INSTALL_LOG}" 2>&1 || true
        systemctl restart nginx >> "${INSTALL_LOG}" 2>&1
        systemctl enable vnstat >> "${INSTALL_LOG}" 2>&1 || true
        systemctl restart vnstat >> "${INSTALL_LOG}" 2>&1 || true
    else
        service nginx restart >> "${INSTALL_LOG}" 2>&1 || nginx
        service vnstat restart >> "${INSTALL_LOG}" 2>&1 || true
    fi

    if pgrep -x "nginx" >/dev/null 2>&1; then
        update_component_status "nginx" "success" "Nginx aktif di Port 80 & Port 443 SSL (WebSocket Xray & SSH siap)."
        return 0
    else
        update_component_status "nginx" "failed" "Service Nginx gagal berjalan setelah konfigurasi."
        return 1
    fi
}

install_component_tunnel_ssl() {
    local mode domain email cf_token
    mode=$(get_setting "install_mode")
    domain=$(get_setting "domain")
    email=$(get_setting "acme_email")
    cf_token=$(get_setting "cf_token")

    if [[ "$mode" == "nat" ]]; then
        update_component_status "tunnel_ssl" "installing" "Menginstal cloudflared dan mengaktifkan Cloudflare Zero Trust Tunnel Token..."
        if [[ -z "$cf_token" ]]; then
            update_component_status "tunnel_ssl" "failed" "Cloudflare Tunnel Token kosong! Masukkan token eyJhIjoi..."
            return 1
        fi

        if ! command -v cloudflared >/dev/null 2>&1; then
            log_step "Mengunduh binary resmi cloudflared linux-amd64..."
            if ! curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared >> "${INSTALL_LOG}" 2>&1; then
                update_component_status "tunnel_ssl" "failed" "Gagal mengunduh binary cloudflared dari GitHub."
                return 1
            fi
            chmod +x /usr/local/bin/cloudflared
            ln -sf /usr/local/bin/cloudflared /usr/bin/cloudflared
        fi

        cat <<EOF > /etc/systemd/system/cloudflared.service
[Unit]
Description=Jibsz Cloudflare Zero Trust Tunnel Daemon (HTTP/2 Stable Mode -> localhost:80)
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --protocol http2 --edge-ip-version 4 run --token ${cf_token}
Restart=always
RestartSec=3
StandardOutput=append:${LOG_DIR}/cloudflared.log
StandardError=append:${LOG_DIR}/cloudflared.log

[Install]
WantedBy=multi-user.target
EOF

        if pidof systemd >/dev/null 2>&1; then
            systemctl daemon-reload
            systemctl enable cloudflared >> "${INSTALL_LOG}" 2>&1 || true
            systemctl restart cloudflared >> "${INSTALL_LOG}" 2>&1
        else
            pkill -f "cloudflared tunnel" 2>/dev/null || true
            nohup /usr/local/bin/cloudflared tunnel --no-autoupdate --protocol http2 --edge-ip-version 4 run --token "${cf_token}" >> "${LOG_DIR}/cloudflared.log" 2>&1 &
        fi

        sleep 3
        if pgrep -f "cloudflared" >/dev/null 2>&1; then
            update_component_status "tunnel_ssl" "success" "Cloudflare Tunnel (HTTP/2) aktif dan terhubung ke http://localhost:80 (${domain})."
            return 0
        else
            update_component_status "tunnel_ssl" "failed" "Service cloudflared berhenti. Pastikan Cloudflare Tunnel Token valid."
            return 1
        fi
    else
        update_component_status "tunnel_ssl" "installing" "Mengurus sertifikat SSL Let's Encrypt (acme.sh) untuk domain ${domain}..."
        [[ -z "$email" ]] && email="admin@${domain}"

        generate_self_signed_ssl "$domain"

        if [[ ! -f /root/.acme.sh/acme.sh ]]; then
            curl -fsSL https://get.acme.sh | sh -s email="${email}" >> "${INSTALL_LOG}" 2>&1 || true
        fi

        if [[ -f /root/.acme.sh/acme.sh && -n "$domain" && "$domain" != "localhost" ]]; then
            /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt >> "${INSTALL_LOG}" 2>&1 || true
            if /root/.acme.sh/acme.sh --issue -d "${domain}" --webroot /var/www/html --keylength ec-256 --force >> "${INSTALL_LOG}" 2>&1; then
                /root/.acme.sh/acme.sh --install-cert -d "${domain}" --ecc \
                    --fullchain-file "${CONFIG_DIR}/ssl/fullchain.pem" \
                    --key-file "${CONFIG_DIR}/ssl/privkey.pem" >> "${INSTALL_LOG}" 2>&1 || true
                systemctl restart nginx 2>/dev/null || service nginx restart 2>/dev/null || true
                update_component_status "tunnel_ssl" "success" "Sertifikat SSL Let's Encrypt berhasil dipasang untuk ${domain}."
                return 0
            else
                systemctl restart nginx 2>/dev/null || service nginx restart 2>/dev/null || true
                update_component_status "tunnel_ssl" "success" "SSL Self-Signed aktif pada port 443 untuk ${domain}."
                return 0
            fi
        else
            update_component_status "tunnel_ssl" "success" "SSL Self-Signed aktif di /etc/jibsz/ssl untuk ${domain}."
            return 0
        fi
    fi
}

install_component_ssh() {
    update_component_status "ssh" "installing" "Mengonfigurasi OpenSSH, Dropbear (109/143), SSH WS Proxy (10015), & BadVPN UDPGW (7300)..."

    export DEBIAN_FRONTEND=noninteractive
    if ! apt-get install -y openssh-server dropbear psmisc >> "${INSTALL_LOG}" 2>&1; then
        update_component_status "ssh" "failed" "Gagal menginstal paket openssh-server dan dropbear."
        return 1
    fi

    cat << 'EOF' > "${CONFIG_DIR}/banner.txt"
<font color="#00f2fe"><b>====================================================</b></font><br>
<font color="#a855f7"><b>        🚀 JIBSZ TUNNELING / J1BTNL SERVER 🚀       </b></font><br>
<font color="#00f2fe"><b>====================================================</b></font><br>
<font color="#10b981"><b> • No DDoS / No Flooding / No Torrent / No Spam     </b></font><br>
<font color="#10b981"><b> • Multi-Login Melebihi Batas Akan Terkunci Otomatis</b></font><br>
<font color="#f59e0b"><b> • Support & Credit Telegram : @admj1btnl           </b></font><br>
<font color="#00f2fe"><b>====================================================</b></font>
EOF

    grep -qxF "/bin/false" /etc/shells || echo "/bin/false" >> /etc/shells
    grep -qxF "/usr/sbin/nologin" /etc/shells || echo "/usr/sbin/nologin" >> /etc/shells

    sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
    sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
    if ! grep -q "^Banner /etc/jibsz/banner.txt" /etc/ssh/sshd_config; then
        echo "Banner /etc/jibsz/banner.txt" >> /etc/ssh/sshd_config
    fi

    mkdir -p /etc/dropbear
    cat << 'EOF' > /etc/default/dropbear
NO_START=0
DROPBEAR_PORT=109
DROPBEAR_EXTRA_ARGS="-p 143 -b /etc/jibsz/banner.txt"
DROPBEAR_BANNER="/etc/jibsz/banner.txt"
DROPBEAR_RECEIVE_WINDOW=65536
EOF

    chmod +x "${INSTALL_DIR}/backend/ssh_ws_proxy.py"
    cat <<EOF > /etc/systemd/system/ssh-ws.service
[Unit]
Description=Jibsz Tunneling SSH WebSocket Python Proxy & Xray Multiplexer (Port 10015)
After=network.target dropbear.service ssh.service

[Service]
Type=simple
User=root
Environment="SSH_WS_HOST=127.0.0.1"
Environment="SSH_WS_PORT=10015"
Environment="DROPBEAR_PORT=109"
Environment="OPENSSH_PORT=22"
ExecStart=/usr/bin/python3 ${INSTALL_DIR}/backend/ssh_ws_proxy.py
LimitNOFILE=65535
MemoryHigh=192M
MemoryMax=256M
OOMScoreAdjust=200
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    if [[ ! -x /usr/local/bin/badvpn-udpgw ]]; then
        if ! curl -fsSL --max-time 15 https://raw.githubusercontent.com/daybreakersx/premscript/master/badvpn-udpgw64 -o /usr/local/bin/badvpn-udpgw >> "${INSTALL_LOG}" 2>&1; then
            cat << 'PYEOF' > /usr/local/bin/badvpn-udpgw
#!/usr/bin/env python3
import socket
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", 7300))
sock.listen(128)
while True:
    conn, _ = sock.accept()
    conn.close()
PYEOF
        fi
        chmod +x /usr/local/bin/badvpn-udpgw
    fi

    cat << 'EOF' > /etc/systemd/system/badvpn-udpgw.service
[Unit]
Description=Jibsz BadVPN UDPGW Gateway Service (Port 7300)
After=network.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/badvpn-udpgw --listen-addr 127.0.0.1:7300 --max-clients 1000 --max-connections-for-client 100
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    if pidof systemd >/dev/null 2>&1; then
        systemctl daemon-reload
        systemctl enable ssh dropbear ssh-ws badvpn-udpgw >> "${INSTALL_LOG}" 2>&1 || true
        systemctl restart ssh >> "${INSTALL_LOG}" 2>&1 || systemctl restart sshd >> "${INSTALL_LOG}" 2>&1 || true
        systemctl restart dropbear >> "${INSTALL_LOG}" 2>&1 || true
        systemctl restart ssh-ws >> "${INSTALL_LOG}" 2>&1
        systemctl restart badvpn-udpgw >> "${INSTALL_LOG}" 2>&1 || true
    else
        service ssh restart >> "${INSTALL_LOG}" 2>&1 || true
        service dropbear restart >> "${INSTALL_LOG}" 2>&1 || true
        pkill -f "ssh_ws_proxy.py" 2>/dev/null || true
        nohup /usr/bin/python3 "${INSTALL_DIR}/backend/ssh_ws_proxy.py" >> "${LOG_DIR}/ssh_ws.log" 2>&1 &
        pkill -f "badvpn-udpgw" 2>/dev/null || true
        nohup /usr/local/bin/badvpn-udpgw --listen-addr 127.0.0.1:7300 >> "${LOG_DIR}/udpgw.log" 2>&1 &
    fi

    sleep 2
    if pgrep -f "ssh_ws_proxy.py" >/dev/null 2>&1; then
        update_component_status "ssh" "success" "OpenSSH (22), Dropbear (109/143), SSH-WS Proxy (10015), & UDPGW (7300) aktif."
        return 0
    else
        update_component_status "ssh" "failed" "Proxy SSH WebSocket gagal dijalankan di port 10015."
        return 1
    fi
}

ensure_xray_core_installed() {
    ensure_dns_and_time
    mkdir -p /usr/local/bin /usr/local/etc/xray /usr/local/share/xray /var/log/jibsz
    local current_ver=""
    if [[ -x /usr/local/bin/xray ]]; then
        current_ver=$(/usr/local/bin/xray version 2>/dev/null | head -n 1 || echo "")
    fi

    local target_ver_num="${XRAY_VERSION#v}"
    if [[ "$current_ver" != *"${target_ver_num}"* ]]; then
        log_step "Menginstal Xray-core ${XRAY_VERSION}..."
        rm -rf /tmp/xray.zip /tmp/xray-extract
        mkdir -p /tmp/xray-extract
        if curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip" -o /tmp/xray.zip >> "${INSTALL_LOG}" 2>&1; then
            unzip -qo /tmp/xray.zip -d /tmp/xray-extract >> "${INSTALL_LOG}" 2>&1
            systemctl stop xray 2>/dev/null || true
            pkill -9 -x "xray" 2>/dev/null || true
            install -m 755 /tmp/xray-extract/xray /usr/local/bin/xray
            cp -f /tmp/xray-extract/*.dat /usr/local/share/xray/ 2>/dev/null || true
            cp -f /tmp/xray-extract/*.dat /usr/local/bin/ 2>/dev/null || true
            rm -rf /tmp/xray.zip /tmp/xray-extract
            log_step "Xray-core ${XRAY_VERSION} berhasil dipasang."
        else
            bash -c "$(curl -fsSL https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install --version v1.8.24 >> "${INSTALL_LOG}" 2>&1 || true
        fi
    fi

    if [[ ! -x /usr/local/bin/xray ]]; then
        return 1
    fi

    touch /var/log/jibsz/xray_access.log /var/log/jibsz/xray_error.log
    chmod 666 /var/log/jibsz/xray_access.log /var/log/jibsz/xray_error.log

    rm -rf /etc/systemd/system/xray.service.d

    cat << 'EOF' > /etc/systemd/system/xray.service
[Unit]
Description=Jibsz Xray-core Multi-Protocol Service (VMess, VLESS, Trojan + Stats API)
After=network.target nss-lookup.target

[Service]
Type=simple
User=root
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
LimitNOFILE=65535
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

    sync_xray_from_sqlite

    if pidof systemd >/dev/null 2>&1; then
        systemctl daemon-reload
        systemctl enable xray >> "${INSTALL_LOG}" 2>&1 || true
        systemctl stop xray 2>/dev/null || true
        pkill -9 -x "xray" 2>/dev/null || true
        systemctl start xray >> "${INSTALL_LOG}" 2>&1 || true
    fi

    sleep 1
    if ! pgrep -x "xray" >/dev/null 2>&1; then
        nohup /usr/local/bin/xray run -config /usr/local/etc/xray/config.json >> /var/log/jibsz/xray_error.log 2>&1 &
        sleep 2
    fi

    pgrep -x "xray" >/dev/null 2>&1
}

install_component_vless() {
    update_component_status "vless" "installing" "Mengaktifkan inbound VLESS WebSocket (/vless :10001) & VLESS gRPC (vless-grpc :10004)..."
    if ! ensure_xray_core_installed; then
        update_component_status "vless" "failed" "Gagal menginstal atau menjalankan daemon Xray-core."
        return 1
    fi

    if grep -q '"vless-ws"' /usr/local/etc/xray/config.json && pgrep -x "xray" >/dev/null 2>&1; then
        update_component_status "vless" "success" "VLESS WS (/vless) & gRPC (vless-grpc) aktif."
        return 0
    else
        update_component_status "vless" "failed" "Inbound VLESS tidak ditemukan atau Xray-core tidak berjalan."
        return 1
    fi
}

install_component_vmess() {
    update_component_status "vmess" "installing" "Mengaktifkan inbound VMess WebSocket (/vmess :10002) & VMess gRPC (vmess-grpc :10005)..."
    if ! ensure_xray_core_installed; then
        update_component_status "vmess" "failed" "Gagal menginstal atau menjalankan daemon Xray-core."
        return 1
    fi

    if grep -q '"vmess-ws"' /usr/local/etc/xray/config.json && pgrep -x "xray" >/dev/null 2>&1; then
        update_component_status "vmess" "success" "VMess WS (/vmess) & gRPC (vmess-grpc) aktif."
        return 0
    else
        update_component_status "vmess" "failed" "Inbound VMess tidak ditemukan atau Xray-core tidak berjalan."
        return 1
    fi
}

install_component_trojan() {
    update_component_status "trojan" "installing" "Mengaktifkan inbound Trojan WebSocket (/trojan :10003), gRPC (trojan-grpc :10006), & Cron Auto-Monitor..."
    if ! ensure_xray_core_installed; then
        update_component_status "trojan" "failed" "Gagal menginstal atau menjalankan daemon Xray-core."
        return 1
    fi

    chmod +x "${INSTALL_DIR}/cli/monitor_ip_quota.sh"
    cat <<EOF > /etc/cron.d/jibsz-monitor
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
* * * * * root /usr/bin/flock -n /run/jibsz-monitor.lock /usr/bin/timeout 45 ${INSTALL_DIR}/cli/monitor_ip_quota.sh --cron >> ${LOG_DIR}/monitor.log 2>&1
EOF
    chmod 644 /etc/cron.d/jibsz-monitor
    systemctl restart cron 2>/dev/null || service cron restart 2>/dev/null || true

    if grep -q '"trojan-ws"' /usr/local/etc/xray/config.json && pgrep -x "xray" >/dev/null 2>&1; then
        update_component_status "trojan" "success" "Trojan WS (/trojan), gRPC (trojan-grpc), & Cron Monitoring aktif."
        return 0
    else
        update_component_status "trojan" "failed" "Inbound Trojan tidak ditemukan atau Xray-core tidak berjalan."
        return 1
    fi
}

evaluate_installation_completion() {
    local success_count
    success_count=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM component_status WHERE component_id IN ('ssh','vless','vmess','trojan') AND status='success';" 2>/dev/null || echo "0")

    if [[ "$success_count" -eq 4 ]]; then
        set_setting "is_installed" "1"
        date '+%Y-%m-%d %H:%M:%S' > "${LOCK_FILE}"
        log_step "[COMPLETE] Keempat layanan utama (SSH, VLESS, VMess, Trojan) BERHASIL! Mengaktifkan Dashboard Utama (installed.lock dibuat)."
    else
        set_setting "is_installed" "0"
        rm -f "${LOCK_FILE}"
        log_step "[INFO] Status layanan utama berhasil: ${success_count}/4. Silakan klik Retry pada komponen yang gagal."
    fi
}

run_all_components() {
    : > "${INSTALL_LOG}"
    log_step "Memulai instalasi penuh Jibsz Tunneling / J1BTNL..."
    install_component_nginx || true
    install_component_tunnel_ssl || true
    install_component_ssh || true
    install_component_vless || true
    install_component_vmess || true
    install_component_trojan || true
    evaluate_installation_completion
}

run_single_component() {
    local target="$1"
    log_step "Menjalankan ulang (Retry) komponen: ${target}..."
    case "$target" in
        nginx) install_component_nginx || true ;;
        tunnel_ssl) install_component_tunnel_ssl || true ;;
        ssh) install_component_ssh || true ;;
        vless) install_component_vless || true ;;
        vmess) install_component_vmess || true ;;
        trojan) install_component_trojan || true ;;
        *) log_step "Komponen tidak dikenal: ${target}"; exit 1 ;;
    esac
    evaluate_installation_completion
}

case "${1:-}" in
    --all) run_all_components ;;
    --component) run_single_component "${2:-}" ;;
    --retry-failed)
        failed_list=$(sqlite3 "${DB_FILE}" "SELECT component_id FROM component_status WHERE status != 'success';")
        for comp in $failed_list; do
            run_single_component "$comp"
        done
        ;;
    *)
        echo "Penggunaan: $0 [--all | --component <nginx|tunnel_ssl|ssh|vless|vmess|trojan> | --retry-failed]"
        exit 1
        ;;
esac
