#!/usr/bin/env bash
set -euo pipefail

export INSTALL_DIR="/opt/jibsz-tunnel"
export LOG_DIR="/var/log/jibsz"
export RAILWAY_MODE="1"

PORT_VAL="${PORT:-8080}"

# Gunakan Railway Persistent Volume (/data) jika terpasang, symlink ke /etc/jibsz
if [[ -d /data ]]; then
    mkdir -p /data/jibsz
    if [[ ! -L /etc/jibsz ]]; then
        rm -rf /etc/jibsz
        ln -s /data/jibsz /etc/jibsz
    fi
else
    mkdir -p /etc/jibsz
fi

export CONFIG_DIR="/etc/jibsz"
export DB_PATH="${CONFIG_DIR}/jibsz.db"

mkdir -p "${CONFIG_DIR}/ssl" "${LOG_DIR}" /root/jibsz-backups /etc/dropbear
touch "${CONFIG_DIR}/blocked_ips.map" "${LOG_DIR}/xray_access.log" "${LOG_DIR}/xray_error.log" "${LOG_DIR}/nginx_access.log" "${LOG_DIR}/nginx_error.log" "${LOG_DIR}/ssh_ws.log" "${LOG_DIR}/monitor.log" "${LOG_DIR}/webpanel.log"
chmod 666 "${LOG_DIR}"/*.log 2>/dev/null || true

# Tentukan domain aktif otomatis dari Railway
DOMAIN_VAL="${PANEL_DOMAIN:-${RAILWAY_PUBLIC_DOMAIN:-localhost}}"
DOMAIN_VAL="${DOMAIN_VAL#https://}"
DOMAIN_VAL="${DOMAIN_VAL#http://}"
DOMAIN_VAL="${DOMAIN_VAL%%/*}"

# Inisialisasi SQLite DB & tandai sudah terinstal agar langsung membuka Dashboard
sqlite3 "${DB_PATH}" <<EOF
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS component_status (
    component_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'success',
    message TEXT DEFAULT 'Aktif di Railway',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol TEXT NOT NULL,
    username TEXT UNIQUE NOT NULL,
    secret TEXT NOT NULL,
    exp_timestamp INTEGER NOT NULL,
    exp_display TEXT NOT NULL,
    is_trial INTEGER DEFAULT 0,
    ip_limit INTEGER DEFAULT 4,
    quota_gb REAL DEFAULT 0,
    up_bytes INTEGER DEFAULT 0,
    down_bytes INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS active_ips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    protocol TEXT NOT NULL,
    client_ip TEXT NOT NULL,
    last_seen INTEGER NOT NULL,
    UNIQUE(username, protocol, client_ip)
);
CREATE TABLE IF NOT EXISTS traffic_daily (
    date_str TEXT PRIMARY KEY,
    up_bytes INTEGER DEFAULT 0,
    down_bytes INTEGER DEFAULT 0
);
INSERT OR REPLACE INTO settings (key, value) VALUES ('panel_port', '${PORT_VAL}');
INSERT OR REPLACE INTO settings (key, value) VALUES ('install_mode', 'railway');
INSERT OR REPLACE INTO settings (key, value) VALUES ('domain', '${DOMAIN_VAL}');
INSERT OR REPLACE INTO settings (key, value) VALUES ('public_ip', '${DOMAIN_VAL}');
INSERT OR REPLACE INTO settings (key, value) VALUES ('is_installed', '1');
INSERT OR IGNORE INTO settings (key, value) VALUES ('autodel_expired', '1');
INSERT OR IGNORE INTO settings (key, value) VALUES ('autodel_unused', '0');
INSERT OR IGNORE INTO settings (key, value) VALUES ('autodel_unused_days', '3');
INSERT OR IGNORE INTO settings (key, value) VALUES ('default_ip_limit', '4');
INSERT OR IGNORE INTO settings (key, value) VALUES ('default_quota_gb', '0');
INSERT OR REPLACE INTO component_status (component_id, name, status, message) VALUES
('nginx', 'Nginx Reverse Proxy (Railway Port)', 'success', 'Aktif'),
('tunnel_ssl', 'Railway Edge TLS (HTTPS/WSS)', 'success', 'Aktif'),
('ssh', 'SSH WebSocket (Dropbear + SSH-WS Proxy)', 'success', 'Aktif'),
('vless', 'VLESS WebSocket (Xray-core)', 'success', 'Aktif'),
('vmess', 'VMess WebSocket (Xray-core)', 'success', 'Aktif'),
('trojan', 'Trojan WebSocket (Xray-core)', 'success', 'Aktif');
EOF

date '+%Y-%m-%d %H:%M:%S' > "${CONFIG_DIR}/installed.lock"

# Pastikan banner SSH default tersedia
if [[ ! -f "${CONFIG_DIR}/banner.txt" ]]; then
    cat << 'BEOF' > "${CONFIG_DIR}/banner.txt"
<font color="#00f2fe"><b>====================================================</b></font><br>
<font color="#a855f7"><b>        JIBSZ TUNNELING / J1BTNL RAILWAY SERVER     </b></font><br>
<font color="#00f2fe"><b>====================================================</b></font>
BEOF
fi

# Re-provision akun SSH dari SQLite ke Linux saat container restart (agar persisten bersama Volume /data)
"${INSTALL_DIR}/venv/bin/python3" - << 'PYREPROV'
import datetime, os, sqlite3, subprocess, sys
sys.path.insert(0, "/opt/jibsz-tunnel/webpanel")
import app as web_app

web_app.ensure_admin_auth_defaults()
try:
    with sqlite3.connect(os.environ["DB_PATH"], timeout=10.0) as conn:
        conn.row_factory = sqlite3.Row
        for r in conn.execute("SELECT username, secret, exp_timestamp, status FROM accounts WHERE protocol='ssh'"):
            exp_dt = datetime.datetime.fromtimestamp(int(r["exp_timestamp"] or 0))
            exp_str = (exp_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            web_app.provision_linux_ssh_user(r["username"], r["secret"], exp_str)
            if (r["status"] or "active") != "active":
                web_app.lock_unlock_linux_ssh_user(r["username"], lock=True)
except Exception as e:
    print(f"[WARN] Re-provision SSH skipped: {e}")

web_app.sync_xray_accounts_to_config(skip_traffic_sync=True)
PYREPROV

# Render Nginx config untuk $PORT Railway
sed "s/__PORT__/${PORT_VAL}/g" "${INSTALL_DIR}/docker/nginx_railway.conf.template" > /etc/nginx/conf.d/default.conf
nginx -t

# Jalankan Dropbear (127.0.0.1:109)
dropbear -p 127.0.0.1:109 -R -b "${CONFIG_DIR}/banner.txt" >> "${LOG_DIR}/ssh_ws.log" 2>&1 &

# Jalankan SSH WebSocket Proxy (127.0.0.1:10015)
/usr/bin/python3 "${INSTALL_DIR}/backend/ssh_ws_proxy.py" >> "${LOG_DIR}/ssh_ws.log" 2>&1 &

# Jalankan FastAPI Web Panel internal (127.0.0.1:8081)
"${INSTALL_DIR}/venv/bin/uvicorn" app:app     --app-dir "${INSTALL_DIR}/webpanel"     --host 127.0.0.1     --port 8081     --workers 1     --timeout-keep-alive 15     --limit-concurrency 64 >> "${LOG_DIR}/webpanel.log" 2>&1 &

# Jalankan background monitor loop pengganti cron (dengan flock & timeout 45s)
(
    while true; do
        sleep 60
        /usr/bin/flock -n /tmp/jibsz-monitor.lock /usr/bin/timeout 45 "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --cron >> "${LOG_DIR}/monitor.log" 2>&1 || true
    done
) &

echo "[INFO] Jibsz Tunneling Railway Edition aktif di port ${PORT_VAL} (Domain: ${DOMAIN_VAL})"

# Jalankan Nginx di foreground sebagai proses utama container
exec nginx -g "daemon off;"
