#!/bin/bash
set -e

export PORT="${PORT:-8080}"
export CONFIG_DIR="${CONFIG_DIR:-/etc/jibsz}"
export LOG_DIR="${LOG_DIR:-/var/log/jibsz}"
export DB_PATH="${DB_PATH:-${CONFIG_DIR}/jibsz.db}"

echo "[JIBSZ-RAILWAY] Menyiapkan direktori & file konfigurasi..."
mkdir -p "$CONFIG_DIR" "$LOG_DIR" /root/jibsz-backups /usr/local/etc/xray /run/sshd /etc/dropbear

# Pastikan log file Xray dapat ditulis tanpa permission denied
touch "$LOG_DIR/xray_access.log" "$LOG_DIR/xray_error.log" "$LOG_DIR/nginx_access.log" "$LOG_DIR/nginx_error.log"
chmod 666 "$LOG_DIR/xray_access.log" "$LOG_DIR/xray_error.log"

if [ ! -f "$CONFIG_DIR/blocked_ips.map" ]; then
    touch "$CONFIG_DIR/blocked_ips.map"
fi

if [ ! -f /usr/local/etc/xray/config.json ]; then
    cp /app/backend/xray_config.json /usr/local/etc/xray/config.json
fi

if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    ssh-keygen -A >/dev/null 2>&1 || true
fi
if [ ! -f /etc/dropbear/dropbear_rsa_host_key ]; then
    dropbearkey -t rsa -f /etc/dropbear/dropbear_rsa_host_key >/dev/null 2>&1 || true
fi

touch "$CONFIG_DIR/installed.lock"

# Prioritaskan DOMAIN (domain Cloudflare Tunnel), fallback ke RAILWAY_PUBLIC_DOMAIN
python3 - <<PYEOF
import os, sqlite3
db_path = os.environ.get("DB_PATH", "/etc/jibsz/jibsz.db")
domain = os.environ.get("DOMAIN") or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "localhost"
conn = sqlite3.connect(db_path)
conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
defaults = {
    "is_installed": "1",
    "install_mode": "nat",
    "domain": domain,
    "panel_port": os.environ.get("PORT", "8080"),
}
for k, v in defaults.items():
    if k in ("domain", "install_mode") and domain != "localhost":
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, v))
    else:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
conn.commit()
conn.close()
print(f"[JIBSZ-RAILWAY] Database siap. Mode: nat (CF Tunnel) | Domain aktif: {domain}")
PYEOF

# Render konfigurasi Nginx dengan $PORT (default 8080)
envsubst '${PORT}' < /app/nginx_railway.conf > /etc/nginx/sites-enabled/default
nginx -t

echo "[JIBSZ-RAILWAY] Menjalankan seluruh service + Cloudflared via Supervisor pada PORT=${PORT}..."
exec /usr/bin/supervisord -n -c /etc/supervisor/supervisord.conf
