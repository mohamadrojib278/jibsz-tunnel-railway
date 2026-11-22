#!/usr/bin/env bash
# ==============================================================================
# JIBSZ TUNNELING / J1BTNL - MAIN CLI & AUTO-DELETE MENU V4.3 (cli/menu.sh)
# Author   : Jibsz Tunneling (@admj1btnl)
# Perintah : menu, jibsz, menu-autodel, menu-system
# Sinkron  : Database SQLite (/etc/jibsz/jibsz.db) & Web Panel V4.3
# ==============================================================================

set -u

INSTALL_DIR="/opt/jibsz-tunnel"
CONFIG_DIR="/etc/jibsz"
LOG_DIR="/var/log/jibsz"
DB_FILE="${CONFIG_DIR}/jibsz.db"
BACKUP_DIR="/root/jibsz-backups"

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

set_setting() {
    sqlite3 "${DB_FILE}" "INSERT OR REPLACE INTO settings (key, value) VALUES ('$1', '$2');" 2>/dev/null || true
}

svc_badge() {
    local svc="$1"
    if systemctl is-active --quiet "$svc" 2>/dev/null || pgrep -f "$svc" >/dev/null 2>&1; then
        echo -e "${C_GREEN}ON${C_RESET}"
    else
        echo -e "${C_RED}OFF${C_RESET}"
    fi
}

show_header() {
    clear
    local os_name kernel uptime_str ram_usage cpu_cores domain pub_ip mode panel_port
    os_name=$(grep '^PRETTY_NAME=' /etc/os-release 2>/dev/null | cut -d= -f2 | tr -d '"')
    [[ -z "$os_name" ]] && os_name="$(uname -s) $(uname -r)"
    kernel=$(uname -r)
    uptime_str=$(uptime -p 2>/dev/null | sed 's/up //')
    ram_usage=$(free -m | awk '/Mem:/ {printf "%d/%d MB (%.0f%%)", $3, $2, ($3/$2)*100}')
    cpu_cores=$(nproc 2>/dev/null || echo "1")
    domain=$(get_setting "domain")
    [[ -z "$domain" ]] && domain="Belum diatur"
    pub_ip=$(get_setting "public_ip")
    [[ -z "$pub_ip" ]] && pub_ip="127.0.0.1"
    mode=$(get_setting "install_mode")
    panel_port=$(get_setting "panel_port")

    local mode_label="VPS IP Public (80/443)"
    [[ "$mode" == "nat" ]] && mode_label="VPS NAT + CF Tunnel (localhost:80)"

    local cnt_ssh cnt_vmess cnt_vless cnt_trojan
    cnt_ssh=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM accounts WHERE protocol='ssh' AND status='active';" 2>/dev/null || echo 0)
    cnt_vmess=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM accounts WHERE protocol='vmess' AND status='active';" 2>/dev/null || echo 0)
    cnt_vless=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM accounts WHERE protocol='vless' AND status='active';" 2>/dev/null || echo 0)
    cnt_trojan=$(sqlite3 "${DB_FILE}" "SELECT COUNT(*) FROM accounts WHERE protocol='trojan' AND status='active';" 2>/dev/null || echo 0)

    echo -e "${C_PURPLE}╔══════════════════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_PURPLE}║${C_CYAN}       🚀 JIBSZ TUNNELING / J1BTNL — CLI PANEL V4.3 🚀            ${C_PURPLE}║${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE}        Telegram: @admj1btnl | GitHub: mohamadrojib278            ${C_PURPLE}║${C_RESET}"
    echo -e "${C_PURPLE}╠══════════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} OS / Kernel  : ${C_CYAN}${os_name} (${kernel})${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} RAM / CPU    : ${C_YELLOW}${ram_usage} | ${cpu_cores} Core | Up: ${uptime_str}${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} Mode Server  : ${C_PURPLE}${mode_label}${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} Domain / IP  : ${C_CYAN}${domain}${C_WHITE} (${pub_ip})${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} Web Panel    : ${C_YELLOW}http://${pub_ip}:${panel_port}${C_RESET}"
    echo -e "${C_PURPLE}╠══════════════════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} Service : NGINX [$(svc_badge nginx)] • XRAY [$(svc_badge xray)] • SSH-WS [$(svc_badge ssh_ws_proxy.py)] • CF [$(svc_badge cloudflared)]${C_RESET}"
    echo -e "${C_PURPLE}║${C_WHITE} Akun    : SSH [${C_GREEN}${cnt_ssh}${C_WHITE}] • VMess [${C_GREEN}${cnt_vmess}${C_WHITE}] • VLESS [${C_GREEN}${cnt_vless}${C_WHITE}] • Trojan [${C_GREEN}${cnt_trojan}${C_WHITE}]${C_RESET}"
    echo -e "${C_PURPLE}╚══════════════════════════════════════════════════════════════════╝${C_RESET}"
}

# ==============================================================================
# SUB-MENU AUTO-DELETE AKUN EXPIRED & TIDAK DIGUNAKAN (IDLE 0 B)
# ==============================================================================
menu_autodelete() {
    while true; do
        show_header
        echo -e "${C_CYAN}  [ MENU AUTO-DELETE AKUN EXPIRED & TIDAK DIGUNAKAN (IDLE) ]${C_RESET}"
        bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --status-autodel
        echo -e "${C_PURPLE}──────────────────────────────────────────────────────────────────${C_RESET}"
        echo -e "  ${C_YELLOW}[1]${C_WHITE} Hapus Semua Akun ${C_RED}EXPIRED${C_WHITE} Sekarang (Instant Clean)"
        echo -e "  ${C_YELLOW}[2]${C_WHITE} Hapus Akun ${C_YELLOW}TIDAK DIGUNAKAN (0 B & Offline)${C_WHITE} Sekarang"
        echo -e "  ${C_YELLOW}[3]${C_WHITE} Hapus Sekaligus (${C_RED}Expired${C_WHITE} + ${C_YELLOW}Tidak Digunakan 0 B${C_WHITE}) Sekarang"
        echo -e "  ${C_YELLOW}[4]${C_WHITE} Aktifkan / Nonaktifkan Jadwal Auto-Delete Akun Expired (Cron)"
        echo -e "  ${C_YELLOW}[5]${C_WHITE} Aktifkan / Atur Jadwal Auto-Delete Akun Tidak Digunakan (Cron)"
        echo -e "  ${C_YELLOW}[0]${C_WHITE} Kembali ke Menu Utama"
        echo -e "${C_PURPLE}──────────────────────────────────────────────────────────────────${C_RESET}"
        read -rp "  Pilih opsi [0-5]: " ad_opt

        case "$ad_opt" in
            1)
                bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --clean-expired
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            2)
                echo ""
                read -rp "  Hapus akun 0 B yang tidak dipakai minimal berapa hari? [0 = Semua akun 0 B]: " min_d
                min_d="${min_d:-0}"
                bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --clean-unused "$min_d"
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            3)
                echo ""
                read -rp "  Batas hari untuk akun tidak digunakan (0 B) [default: 0 = Semua akun 0 B]: " min_d
                min_d="${min_d:-0}"
                bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --clean-all "$min_d"
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            4)
                local cur_exp
                cur_exp=$(get_setting "autodel_expired")
                if [[ "$cur_exp" == "0" ]]; then
                    set_setting "autodel_expired" "1"
                    echo -e "\n  ${C_GREEN}[✓] Auto-Delete Akun Expired Otomatis (Cron) DIAKTIFKAN!${C_RESET}"
                else
                    set_setting "autodel_expired" "0"
                    echo -e "\n  ${C_YELLOW}[i] Auto-Delete Akun Expired Otomatis DINONAKTIFKAN (Hanya Lock).${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            5)
                echo ""
                echo -e "  Pilih mode Auto-Delete Otomatis untuk Akun Tidak Digunakan (0 B & Offline):"
                echo -e "    ${C_YELLOW}[1]${C_WHITE} Aktifkan — Hapus otomatis akun 0 B setelah N hari"
                echo -e "    ${C_YELLOW}[2]${C_WHITE} Nonaktifkan Auto-Delete Akun Tidak Digunakan"
                read -rp "  Pilih [1/2]: " u_mode
                if [[ "$u_mode" == "1" ]]; then
                    read -rp "  Hapus otomatis akun 0 B yang tidak dipakai setelah berapa hari? [default: 3]: " u_days
                    u_days="${u_days:-3}"
                    set_setting "autodel_unused" "1"
                    set_setting "autodel_unused_days" "$u_days"
                    echo -e "\n  ${C_GREEN}[✓] Auto-Delete Akun Tidak Digunakan (0 B > ${u_days} hari) DIAKTIFKAN!${C_RESET}"
                else
                    set_setting "autodel_unused" "0"
                    echo -e "\n  ${C_YELLOW}[i] Auto-Delete Akun Tidak Digunakan DINONAKTIFKAN.${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            0)
                break
                ;;
            *)
                ;;
        esac
    done
}

# ==============================================================================
# SUB-MENU SYSTEM (menu-system)
# ==============================================================================
menu_system() {
    while true; do
        show_header
        echo -e "${C_CYAN}  [ MENU SYSTEM & PENGATURAN SERVER ]${C_RESET}"
        echo -e "  ${C_YELLOW}[1]${C_WHITE} Ganti Domain / Subdomain Server & Perbarui Nginx/SSL"
        echo -e "  ${C_YELLOW}[2]${C_WHITE} Update Token Cloudflare Zero Trust Tunnel (Mode VPS NAT)"
        echo -e "  ${C_YELLOW}[3]${C_WHITE} Ganti Custom Port Web Panel Jibsz"
        echo -e "  ${C_YELLOW}[4]${C_WHITE} Restart Semua Service Tunneling & Web Panel"
        echo -e "  ${C_YELLOW}[5]${C_WHITE} Menu Auto-Delete Akun Expired & Akun Tidak Digunakan"
        echo -e "  ${C_YELLOW}[6]${C_WHITE} Backup Database Akun & Konfigurasi (/root/jibsz-backups)"
        echo -e "  ${C_YELLOW}[7]${C_WHITE} Restore Database Akun & Konfigurasi"
        echo -e "  ${C_YELLOW}[0]${C_WHITE} Kembali ke Menu Utama / Keluar"
        echo -e "${C_PURPLE}──────────────────────────────────────────────────────────────────${C_RESET}"
        read -rp "  Pilih opsi [0-7]: " sys_opt

        case "$sys_opt" in
            1)
                read -rp "  Masukkan Domain / Subdomain baru: " new_domain
                if [[ -n "$new_domain" ]]; then
                    set_setting "domain" "$new_domain"
                    bash "${INSTALL_DIR}/backend/installer_engine.sh" --component nginx
                    bash "${INSTALL_DIR}/backend/installer_engine.sh" --component tunnel_ssl
                    echo -e "  ${C_GREEN}Domain berhasil diperbarui ke ${new_domain}!${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            2)
                read -rp "  Masukkan Cloudflare Tunnel Token baru (eyJhIjoi...): " new_token
                if [[ -n "$new_token" ]]; then
                    set_setting "install_mode" "nat"
                    set_setting "cf_token" "$new_token"
                    bash "${INSTALL_DIR}/backend/installer_engine.sh" --component nginx
                    bash "${INSTALL_DIR}/backend/installer_engine.sh" --component tunnel_ssl
                    echo -e "  ${C_GREEN}Token Cloudflare Tunnel berhasil diperbarui dan cloudflared di-restart!${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            3)
                read -rp "  Masukkan Custom Port Web Panel baru (81-65535): " new_port
                if [[ "$new_port" =~ ^[0-9]+$ ]] && (( new_port >= 81 && new_port <= 65535 )) && (( new_port != 443 )); then
                    set_setting "panel_port" "$new_port"
                    sed -i "s/^PANEL_PORT=.*/PANEL_PORT=${new_port}/" "${CONFIG_DIR}/panel.env" 2>/dev/null || true
                    sed -i "s/--port [0-9]\+/--port ${new_port}/g" /etc/systemd/system/jibsz-webpanel.service 2>/dev/null || true
                    systemctl daemon-reload 2>/dev/null || true
                    systemctl restart jibsz-webpanel 2>/dev/null || true
                    iptables -I INPUT -p tcp --dport "${new_port}" -j ACCEPT 2>/dev/null || true
                    echo -e "  ${C_GREEN}Port Web Panel berhasil diubah ke ${new_port}!${C_RESET}"
                else
                    echo -e "  ${C_RED}Port tidak valid!${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            4)
                echo -e "  ${C_CYAN}Me-restart seluruh layanan...${C_RESET}"
                for s in nginx xray ssh dropbear ssh-ws cloudflared badvpn-udpgw cron jibsz-webpanel; do
                    systemctl restart "$s" 2>/dev/null || true
                done
                echo -e "  ${C_GREEN}Semua service telah di-restart!${C_RESET}"
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            5)
                menu_autodelete
                ;;
            6)
                mkdir -p "${BACKUP_DIR}"
                local ts_file="${BACKUP_DIR}/jibsz-backup-$(date '+%Y%m%d-%H%M%S').tar.gz"
                tar -czf "$ts_file" "${CONFIG_DIR}" /usr/local/etc/xray/config.json 2>/dev/null || true
                echo -e "  ${C_GREEN}Backup berhasil disimpan di: ${ts_file}${C_RESET}"
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            7)
                echo -e "  Daftar file backup di ${BACKUP_DIR}:"
                ls -1 "${BACKUP_DIR}"/*.tar.gz 2>/dev/null || echo "  (Belum ada file backup)"
                read -rp "  Masukkan path lengkap file backup (.tar.gz) untuk di-restore: " restore_path
                if [[ -f "$restore_path" ]]; then
                    tar -xzf "$restore_path" -C /
                    systemctl restart xray nginx ssh-ws jibsz-webpanel 2>/dev/null || true
                    echo -e "  ${C_GREEN}Database & konfigurasi berhasil di-restore!${C_RESET}"
                else
                    echo -e "  ${C_RED}File tidak ditemukan.${C_RESET}"
                fi
                read -rp "  Tekan Enter untuk melanjutkan..."
                ;;
            0)
                break
                ;;
            *)
                ;;
        esac
    done
}

# Jika dipanggil langsung sebagai menu-system atau menu-autodel
if [[ "$(basename "$0")" == "menu-system" || "${1:-}" == "system" ]]; then
    menu_system
    exit 0
fi

if [[ "$(basename "$0")" == "menu-autodel" || "${1:-}" == "autodel" ]]; then
    menu_autodelete
    exit 0
fi

# ==============================================================================
# MENU INTERAKTIF UTAMA (menu / jibsz)
# ==============================================================================
while true; do
    show_header
    echo -e "${C_CYAN}  [ DAFTAR MENU MANAJEMEN JIBSZ TUNNELING V4.3 ]${C_RESET}"
    echo -e "  ${C_YELLOW}[1]${C_WHITE} Menu SSH & OpenVPN WebSocket (${C_GREEN}menu-ssh${C_WHITE})"
    echo -e "  ${C_YELLOW}[2]${C_WHITE} Menu VMess Xray WS & gRPC    (${C_GREEN}menu-vmess${C_WHITE})"
    echo -e "  ${C_YELLOW}[3]${C_WHITE} Menu VLESS Xray WS & gRPC    (${C_GREEN}menu-vless${C_WHITE})"
    echo -e "  ${C_YELLOW}[4]${C_WHITE} Menu Trojan Xray WS & gRPC   (${C_GREEN}menu-trojan${C_WHITE})"
    echo -e "  ${C_YELLOW}[5]${C_WHITE} Cek Monitoring Real IP Client & Kuota Live"
    echo -e "  ${C_YELLOW}[6]${C_WHITE} Auto-Delete Akun Expired & Tidak Digunakan (${C_GREEN}menu-autodel${C_WHITE})"
    echo -e "  ${C_YELLOW}[7]${C_WHITE} Menu System, Domain, CF Token, & Backup (${C_GREEN}menu-system${C_WHITE})"
    echo -e "  ${C_YELLOW}[0]${C_WHITE} Keluar dari Menu"
    echo -e "${C_PURPLE}──────────────────────────────────────────────────────────────────${C_RESET}"
    read -rp "  Pilih menu [0-7]: " main_opt

    case "$main_opt" in
        1)
            bash "${INSTALL_DIR}/cli/manage_ssh.sh"
            ;;
        2)
            bash "${INSTALL_DIR}/cli/manage_xray.sh" vmess
            ;;
        3)
            bash "${INSTALL_DIR}/cli/manage_xray.sh" vless
            ;;
        4)
            bash "${INSTALL_DIR}/cli/manage_xray.sh" trojan
            ;;
        5)
            bash "${INSTALL_DIR}/cli/monitor_ip_quota.sh" --report
            read -rp "  Tekan Enter untuk kembali ke menu..."
            ;;
        6)
            menu_autodelete
            ;;
        7)
            menu_system
            ;;
        0|q|exit)
            clear
            exit 0
            ;;
        *)
            ;;
    esac
done
