#!/usr/bin/env bash
# ==============================================================================
# JIBSZ TUNNELING / J1BTNL - SSH & WEBSOCKET CLI MANAGER V4.3 (cli/manage_ssh.sh)
# Author   : Jibsz Tunneling (@admj1btnl)
# Perintah : menu-ssh
# Format   : host:port@username:password
# ==============================================================================

set -u

INSTALL_DIR="/opt/jibsz-tunnel"
CONFIG_DIR="/etc/jibsz"
DB_FILE="${CONFIG_DIR}/jibsz.db"

C_RESET="\033[0m"
C_CYAN="\033[1;36m"
C_PURPLE="\033[1;35m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_RED="\033[1;31m"
C_WHITE="\033[1;37m"

get_setting() {
    sqlite3 "${DB_FILE}" "SELECT value FROM settings WHERE key='$1' LIMIT 1;" 2>/dev/null || echo ""
}

list_ssh_table_cli() {
    python3 - << 'PYEOF'
import sqlite3
db_path = "/etc/jibsz/jibsz.db"
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, username, status, ip_limit, quota_gb, up_bytes, down_bytes, exp_display FROM accounts WHERE protocol='ssh' ORDER BY id ASC").fetchall()
print("\033[1;35m──────────────────────────────────────────────────────────────────\033[0m")
print(f"\033[1;36m {'ID':<4} {'USERNAME':<14} {'STATUS':<9} {'USAGE':<12} {'EXPIRED':<16}\033[0m")
print("\033[1;35m──────────────────────────────────────────────────────────────────\033[0m")
if not rows:
    print(" \033[1;33m(Belum ada akun SSH terdaftar)\033[0m")
else:
    for r in rows:
        tot_mb = (int(r["up_bytes"] or 0) + int(r["down_bytes"] or 0)) / (1024 * 1024)
        u_str = f"{tot_mb:.2f} MB" if tot_mb < 1024 else f"{tot_mb/1024:.2f} GB"
        print(f" {r['id']:<4} {r['username'][:14]:<14} {r['status'].upper():<9} {u_str:<12} {r['exp_display']:<16}")
print("\033[1;35m──────────────────────────────────────────────────────────────────\033[0m")
conn.close()
PYEOF
}

print_ssh_account_Summary() {
    local user="$1"
    local pass="$2"
    local exp_disp="$3"
    local ip_lim="$4"
    local quota_gb="$5"
    local domain pub_ip
    domain=$(get_setting "domain")
    pub_ip=$(get_setting "public_ip")
    [[ -z "$domain" ]] && domain="$pub_ip"

    echo ""
    echo -e "${C_PURPLE}╔══════════════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_PURPLE}║${C_GREEN}            DETAIL AKUN SSH & WEBSOCKET (J1BTNL V4.3)             ${C_PURPLE}║${C_RESET}"
    echo -e "${C_PURPLE}╠══════════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "  Format SSH TLS (443)  : ${C_GREEN}${domain}:443@${user}:${pass}${C_RESET}"
    echo -e "  Format SSH NTLS (80)  : ${C_GREEN}${domain}:80@${user}:${pass}${C_RESET}"
    echo -e "  Host / Domain         : ${C_CYAN}${domain}${C_RESET}"
    echo -e "  IP Public VPS         : ${C_CYAN}${pub_ip}${C_RESET}"
    echo -e "  Username              : ${C_YELLOW}${user}${C_RESET}"
    echo -e "  Password              : ${C_YELLOW}${pass}${C_RESET}"
    echo -e "  Port OpenSSH/Dropbear : 22, 109, 143 | UDPGW: 7300"
    echo -e "  Path WebSocket        : ${C_GREEN}/ssh-ws${C_RESET} (atau ${C_GREEN}/${C_RESET})"
    echo -e "  Limit IP / Kuota      : ${ip_lim} IP / ${quota_gb} GB"
    echo -e "  Masa Aktif s/d        : ${C_CYAN}${exp_disp}${C_RESET}"
    echo -e "${C_PURPLE}╠══════════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "  ${C_WHITE}Payload Dark++ / HTTP Custom (WSS & WS):${C_RESET}"
    echo -e "  ${C_GREEN}GET /cdn-cgi/trace HTTP/1.1[crlf]Host: [host][crlf][crlf]GET-WSS / HTTP/1.1[crlf]Host: [host][crlf]User-Agent: [ua][crlf]Upgrade: Websocket[crlf][crlf]${C_RESET}"
    echo -e "${C_PURPLE}╚══════════════════════════════════════════════════════════════════╝${C_RESET}"
}

create_ssh_cli() {
    local is_trial="${1:-0}"
    local user pass days mins ip_limit quota_gb exp_ts exp_disp exp_linux

    if [[ "$is_trial" == "1" ]]; then
        user="trialssh$(shuf -i 100-999 -n 1)"
        pass="123"
        read -rp "  Durasi Trial SSH dalam menit [default: 60]: " mins
        mins="${mins:-60}"
        ip_limit=2
        quota_gb=2
        exp_ts=$(( $(date +%s) + (mins * 60) ))
        exp_disp=$(date -d "@${exp_ts}" '+%Y-%m-%d %H:%M')
        exp_linux=$(date -d "+2 days" '+%Y-%m-%d')
    else
        read -rp "  Masukkan Username SSH baru : " user
        [[ -z "$user" ]] && return
        read -rp "  Masukkan Password SSH      : " pass
        [[ -z "$pass" ]] && pass="12345"
        read -rp "  Masa Aktif (hari) [30]     : " days
        days="${days:-30}"
        read -rp "  Limit IP Login [2]         : " ip_limit
        ip_limit="${ip_limit:-2}"
        read -rp "  Limit Kuota (GB) [50]      : " quota_gb
        quota_gb="${quota_gb:-50}"
        exp_ts=$(( $(date +%s) + (days * 86400) ))
        exp_disp=$(date -d "@${exp_ts}" '+%Y-%m-%d %H:%M')
        exp_linux=$(date -d "@$(( exp_ts + 86400 ))" '+%Y-%m-%d')
    fi

    local exists
    exists=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM accounts WHERE LOWER(username)=LOWER('${user}');")
    if [[ "$exists" -gt 0 ]]; then
        echo -e "  ${C_RED}Error: Username '${user}' sudah ada di database!${C_RESET}"
        return
    fi

    useradd -e "${exp_linux}" -s /bin/false -M "${user}" 2>/dev/null || usermod -e "${exp_linux}" "${user}" 2>/dev/null || true
    echo "${user}:${pass}" | chpasswd 2>/dev/null || true

    sqlite3 "${DB_FILE}" "INSERT INTO accounts (protocol, username, secret, exp_timestamp, exp_display, is_trial, ip_limit, quota_gb, up_bytes, down_bytes, status) VALUES ('ssh', '${user}', '${pass}', ${exp_ts}, '${exp_disp}', ${is_trial}, ${ip_limit}, ${quota_gb}, 0, 0, 'active');"

    print_ssh_account_Summary "$user" "$pass" "$exp_disp" "$ip_limit" "$quota_gb"
}

renew_ssh_cli() {
    list_ssh_table_cli
    read -rp "  Masukkan Username SSH yang akan diperpanjang: " user
    [[ -z "$user" ]] && return
    read -rp "  Tambah masa aktif berapa hari? [30]: " add_days
    add_days="${add_days:-30}"

    local curr_exp
    curr_exp=$(sqlite3 "${DB_FILE}" "SELECT exp_timestamp FROM accounts WHERE LOWER(username)=LOWER('${user}') AND protocol='ssh';")
    if [[ -z "$curr_exp" ]]; then
        echo -e "  ${C_RED}Akun SSH '${user}' tidak ditemukan.${C_RESET}"
        return
    fi

    local now_ts base_ts new_ts new_disp new_linux
    now_ts=$(date +%s)
    base_ts=$(( curr_exp > now_ts ? curr_exp : now_ts ))
    new_ts=$(( base_ts + (add_days * 86400) ))
    new_disp=$(date -d "@${new_ts}" '+%Y-%m-%d %H:%M')
    new_linux=$(date -d "@$(( new_ts + 86400 ))" '+%Y-%m-%d')

    usermod -e "${new_linux}" -U "${user}" 2>/dev/null || true
    sqlite3 "${DB_FILE}" "UPDATE accounts SET exp_timestamp=${new_ts}, exp_display='${new_disp}', status='active', is_trial=0 WHERE LOWER(username)=LOWER('${user}') AND protocol='ssh';"
    echo -e "  ${C_GREEN}Akun SSH '${user}' berhasil diperpanjang hingga ${new_disp}!${C_RESET}"
}

delete_ssh_cli() {
    list_ssh_table_cli
    read -rp "  Masukkan Username SSH yang akan dihapus: " user
    [[ -z "$user" ]] && return

    pkill -u "$user" 2>/dev/null || true
    userdel -f "$user" 2>/dev/null || true
    python3 - "$user" << 'PYEOF'
import sqlite3, sys
sys.path.insert(0, "/opt/jibsz-tunnel/webpanel")
import app as web_app
uname = sys.argv[1]
with web_app.get_db() as conn:
    row = conn.execute("SELECT id, username, up_bytes, down_bytes FROM accounts WHERE LOWER(username)=LOWER(?) AND protocol='ssh'", (uname,)).fetchone()
    if row:
        web_app.deduct_traffic_from_daily(conn, int(row["up_bytes"] or 0), int(row["down_bytes"] or 0))
        conn.execute("DELETE FROM accounts WHERE id = ?", (row["id"],))
        conn.execute("DELETE FROM active_ips WHERE LOWER(username)=LOWER(?) AND protocol='ssh'", (uname,))
        web_app.reconcile_daily_traffic_with_accounts(conn)
        conn.commit()
PYEOF
    echo -e "  ${C_GREEN}Akun SSH '${user}' berhasil dihapus & Usage disinkronkan.${C_RESET}"
}

lock_unlock_ssh_cli() {
    list_ssh_table_cli
    read -rp "  Masukkan Username SSH untuk Lock/Unlock: " user
    [[ -z "$user" ]] && return

    local st
    st=$(sqlite3 "${DB_FILE}" "SELECT status FROM accounts WHERE LOWER(username)=LOWER('${user}') AND protocol='ssh';")
    if [[ "$st" == "active" ]]; then
        usermod -L "$user" 2>/dev/null || true
        pkill -u "$user" 2>/dev/null || true
        sqlite3 "${DB_FILE}" "UPDATE accounts SET status='locked' WHERE LOWER(username)=LOWER('${user}') AND protocol='ssh';"
        echo -e "  ${C_YELLOW}Akun '${user}' telah DIKUNCI (Locked).${C_RESET}"
    elif [[ -n "$st" ]]; then
        usermod -U "$user" 2>/dev/null || true
        sqlite3 "${DB_FILE}" "DELETE FROM active_ips WHERE LOWER(username)=LOWER('${user}'); UPDATE accounts SET status='active' WHERE LOWER(username)=LOWER('${user}') AND protocol='ssh';"
        echo -e "  ${C_GREEN}Akun '${user}' telah DIBUKA (Active).${C_RESET}"
    else
        echo -e "  ${C_RED}Akun tidak ditemukan.${C_RESET}"
    fi
}

check_ssh_login_cli() {
    bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --report
}

while true; do
    clear
    echo -e "${C_PURPLE}╔══════════════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_PURPLE}║${C_CYAN}         MENU MANAJEMEN SSH & WEBSOCKET (JIBSZ V4.3)              ${C_PURPLE}║${C_RESET}"
    echo -e "${C_PURPLE}╚══════════════════════════════════════════════════════════════════╝${C_RESET}"
    echo -e "  ${C_YELLOW}[1]${C_WHITE} Buat Akun SSH / WebSocket Baru"
    echo -e "  ${C_YELLOW}[2]${C_WHITE} Buat Akun Trial SSH (Menit)"
    echo -e "  ${C_YELLOW}[3]${C_WHITE} Perpanjang Masa Aktif Akun SSH (Renew)"
    echo -e "  ${C_YELLOW}[4]${C_WHITE} Hapus Akun SSH (Delete)"
    echo -e "  ${C_YELLOW}[5]${C_WHITE} Kunci / Buka Kunci Akun SSH (Lock / Unlock)"
    echo -e "  ${C_YELLOW}[6]${C_WHITE} Cek User Login & Real IP Client Live"
    echo -e "  ${C_YELLOW}[0]${C_WHITE} Kembali ke Menu Utama"
    echo -e "${C_PURPLE}──────────────────────────────────────────────────────────────────${C_RESET}"
    read -rp "  Pilih opsi [0-6]: " opt
    case "$opt" in
        1) create_ssh_cli 0; read -rp "  Tekan Enter untuk lanjut..." ;;
        2) create_ssh_cli 1; read -rp "  Tekan Enter untuk lanjut..." ;;
        3) renew_ssh_cli; read -rp "  Tekan Enter untuk lanjut..." ;;
        4) delete_ssh_cli; read -rp "  Tekan Enter untuk lanjut..." ;;
        5) lock_unlock_ssh_cli; read -rp "  Tekan Enter untuk lanjut..." ;;
        6) check_ssh_login_cli; read -rp "  Tekan Enter untuk lanjut..." ;;
        0) break ;;
        *) ;;
    esac
done
