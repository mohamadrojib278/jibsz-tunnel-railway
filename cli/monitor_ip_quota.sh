#!/usr/bin/env bash
# ==============================================================================
# JIBSZ TUNNELING / J1BTNL - DAEMON, CRON MONITOR IP/KUOTA, & AUTO-BLOCK V4.6
# File     : cli/monitor_ip_quota.sh
# Author   : Jibsz Tunneling (@admj1btnl)
# Fitur    : - Menggunakan engine tunggal webpanel/app.py untuk sinkronisasi
#              Real-Time Live Client IP, Usage Akun/Server, & Auto-Block (status='blocked')
#              saat melebihi batas Devices Limit (ip_limit) atau Kuota (quota_gb)
#            - Mendukung --cron, --sync-only, --report (Menu [5] CLI),
#              --status-autodel, --clean-expired, --clean-unused [hari],
#              dan --clean-all [hari] (Menu [6] Auto-Delete CLI)
# ==============================================================================

set -u

INSTALL_DIR="/opt/jibsz-tunnel"
CONFIG_DIR="/etc/jibsz"
LOG_DIR="/var/log/jibsz"
DB_FILE="${CONFIG_DIR}/jibsz.db"

MODE_ARG="${1:---cron}"
EXTRA_ARG="${2:-0}"

PY_BIN="python3"
if [[ -x "${INSTALL_DIR}/venv/bin/python3" ]]; then
    PY_BIN="${INSTALL_DIR}/venv/bin/python3"
fi

"$PY_BIN" - "$MODE_ARG" "$EXTRA_ARG" << 'PYEOF'
import calendar
import datetime
import os
import sqlite3
import subprocess
import sys
import time
from typing import Dict, List

mode_arg = sys.argv[1] if len(sys.argv) > 1 else "--cron"
extra_arg = sys.argv[2] if len(sys.argv) > 2 else "0"
db_path = "/etc/jibsz/jibsz.db"

C_RESET = "\033[0m"
C_CYAN = "\033[1;36m"
C_PURPLE = "\033[1;35m"
C_GREEN = "\033[1;32m"
C_YELLOW = "\033[1;33m"
C_RED = "\033[1;31m"
C_WHITE = "\033[1;37m"

if not os.path.exists(db_path):
    if mode_arg in ("--report", "--status-autodel", "--clean-expired", "--clean-unused", "--clean-all"):
        print(f"\n  {C_RED}[!] Database /etc/jibsz/jibsz.db belum ditemukan.{C_RESET}\n")
    raise SystemExit(0)

sys.path.insert(0, "/opt/jibsz-tunnel/webpanel")
try:
    import app as web_app
except Exception as exc:
    if mode_arg == "--report":
        print(f"\n  {C_RED}[!] Gagal memuat modul webpanel: {exc}{C_RESET}\n")
    raise SystemExit(0)

# 1. Jalankan sinkronisasi live trafik, Real Client IP, & Auto-Block (Device/Kuota)
web_app.sync_live_traffic_and_ips(force=True)

if mode_arg == "--sync-only":
    raise SystemExit(0)

now_ts = int(time.time())
conn = sqlite3.connect(db_path, timeout=20.0)
conn.row_factory = sqlite3.Row


def fmt_bytes(b: float) -> str:
    b = float(max(0.0, b or 0.0))
    if b < 1024:
        return f"{int(b)} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.2f} KB"
    if b < 1024 ** 3:
        return f"{b / (1024 ** 2):.2f} MB"
    return f"{b / (1024 ** 3):.2f} GB"


# 2. Mode --report: Tabel Monitoring IP Client & Kuota Real-Time
if mode_arg == "--report":
    rows = conn.execute("SELECT * FROM accounts ORDER BY protocol ASC, username ASC").fetchall()
    ip_rows = conn.execute(
        "SELECT username, protocol, client_ip FROM active_ips WHERE last_seen >= ?",
        (now_ts - 45,),
    ).fetchall()

    ip_map: Dict[str, List[str]] = {}
    for ir in ip_rows:
        k = f"{ir['protocol']}:{ir['username'].lower()}"
        if ir["client_ip"] not in ip_map.setdefault(k, []):
            ip_map[k].append(ir["client_ip"])

    print("")
    print(f"{C_PURPLE}╔══════════════════════════════════════════════════════════════════╗{C_RESET}")
    print(f"{C_PURPLE}║{C_CYAN}     📊 MONITORING REAL IP CLIENT & KUOTA LIVE (J1BTNL V4.6)      {C_PURPLE}║{C_RESET}")
    print(f"{C_PURPLE}╠════════╦════════════╦═════════╦═════════╦═══════════════╦════════╣{C_RESET}")
    print(f"{C_PURPLE}║{C_WHITE} PROTO  {C_PURPLE}║{C_WHITE} USERNAME   {C_PURPLE}║{C_WHITE} STATUS  {C_PURPLE}║{C_WHITE} DEVICES {C_PURPLE}║{C_WHITE} USAGE / LIMIT {C_PURPLE}║{C_WHITE} EXP    {C_PURPLE}║{C_RESET}")
    print(f"{C_PURPLE}╠════════╬════════════╬═════════╬═════════╬═══════════════╬════════╣{C_RESET}")

    tot_up_all = 0
    tot_dn_all = 0
    if not rows:
        print(f"{C_PURPLE}║{C_YELLOW}             Belum ada akun terdaftar di database.                {C_PURPLE}║{C_RESET}")
    else:
        for r in rows:
            proto = r["protocol"].upper()[:6]
            uname = r["username"][:10]
            raw_st = (r["status"] or "active").lower()
            st_disp = "ACTIVE" if raw_st == "active" else ("EXPIRED" if raw_st == "expired" else "BLOCKED")
            st_col = C_GREEN if st_disp == "ACTIVE" else C_RED

            k = f"{r['protocol']}:{r['username'].lower()}"
            ips = ip_map.get(k, []) if raw_st == "active" else []
            ip_cnt_str = f"{len(ips)}/{r['ip_limit']} IP"
            ip_col = C_CYAN if len(ips) > 0 else C_WHITE

            up_b = int(r["up_bytes"] or 0)
            dn_b = int(r["down_bytes"] or 0)
            tot_up_all += up_b
            tot_dn_all += dn_b
            used_b = up_b + dn_b
            q_gb = float(r["quota_gb"] or 0)
            lim_str = "Unli" if q_gb <= 0 else f"{q_gb:.0f}G"
            usage_str = f"{fmt_bytes(used_b)}/{lim_str}"[:13]

            exp_short = ((r["exp_display"] or "").split(" ")[0])[5:10]

            print(
                f"{C_PURPLE}║{C_CYAN} {proto:<6} {C_PURPLE}║{C_WHITE} {uname:<10} {C_PURPLE}║{st_col} {st_disp:<7} {C_PURPLE}║{ip_col} {ip_cnt_str:<7} {C_PURPLE}║{C_YELLOW} {usage_str:<13} {C_PURPLE}║{C_WHITE} {exp_short:<6} {C_PURPLE}║{C_RESET}"
            )
            if ips:
                ip_list_str = ", ".join(ips)[:50]
                print(f"{C_PURPLE}║{C_GREEN}  ↳ Real IP: {C_CYAN}{ip_list_str:<53}{C_PURPLE}║{C_RESET}")

    print(f"{C_PURPLE}╠════════╩════════════╩═════════╩═════════╩═══════════════╩════════╣{C_RESET}")
    tot_summary = f"Total Usage: {fmt_bytes(tot_up_all + tot_dn_all)} (↑ {fmt_bytes(tot_up_all)} / ↓ {fmt_bytes(tot_dn_all)})"
    print(f"{C_PURPLE}║{C_GREEN} {tot_summary:<64} {C_PURPLE}║{C_RESET}")
    print(f"{C_PURPLE}╚══════════════════════════════════════════════════════════════════╝{C_RESET}")
    print("")
    conn.close()
    raise SystemExit(0)

# 3. Mode --status-autodel
if mode_arg == "--status-autodel":
    rows = conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()
    online_rows = conn.execute(
        "SELECT DISTINCT username, protocol FROM active_ips WHERE last_seen >= ?",
        (now_ts - 45,),
    ).fetchall()
    online_keys = {f"{r['protocol']}:{r['username'].lower()}" for r in online_rows}

    exp_list = []
    unused_list = []
    for r in rows:
        uname = r["username"]
        proto = r["protocol"].upper()
        exp_ts = int(r["exp_timestamp"] or 0)
        st = (r["status"] or "active").lower()
        tot_b = int(r["up_bytes"] or 0) + int(r["down_bytes"] or 0)
        is_on = f"{r['protocol']}:{uname.lower()}" in online_keys

        if now_ts >= exp_ts or st == "expired":
            exp_list.append(f"{uname} ({proto})")
        elif tot_b <= 0 and not is_on:
            unused_list.append(f"{uname} ({proto})")

    autodel_exp = web_app.get_setting("autodel_expired", "1")
    autodel_unused = web_app.get_setting("autodel_unused", "0")
    unused_days = web_app.get_setting("autodel_unused_days", "3")

    exp_badge = f"{C_GREEN}AKTIF (Otomatis Hapus){C_RESET}" if autodel_exp == "1" else f"{C_YELLOW}NONAKTIF (Hanya Block){C_RESET}"
    unused_badge = (
        f"{C_GREEN}AKTIF (Hapus 0 B > {unused_days} Hari){C_RESET}"
        if autodel_unused == "1"
        else f"{C_RED}NONAKTIF{C_RESET}"
    )

    print(f"  • Jadwal Auto-Delete Expired        : {exp_badge}")
    print(f"  • Jadwal Auto-Delete Tidak Terpakai : {unused_badge}")
    print(f"  • Akun Expired Saat Ini             : {C_RED}{len(exp_list)} akun{C_RESET}" + (f" -> {', '.join(exp_list[:6])}" if exp_list else ""))
    print(f"  • Akun Tidak Digunakan (0 B & Off)  : {C_YELLOW}{len(unused_list)} akun{C_RESET}" + (f" -> {', '.join(unused_list[:6])}" if unused_list else ""))
    conn.close()
    raise SystemExit(0)

# 4. Mode Eksekusi Pembersihan (--clean-expired, --clean-unused, --clean-all)
if mode_arg in ("--clean-expired", "--clean-unused", "--clean-all"):
    conn.close()
    try:
        min_d = float(extra_arg or 0.0)
    except Exception:
        min_d = 0.0

    do_exp = mode_arg in ("--clean-expired", "--clean-all")
    do_unused = mode_arg in ("--clean-unused", "--clean-all")
    res = web_app.clean_expired_and_unused_accounts(
        clean_expired=do_exp,
        clean_unused=do_unused,
        unused_min_days=min_d,
    )
    del_exp = res.get("deleted_expired", [])
    del_un = res.get("deleted_unused", [])

    print("")
    if do_exp:
        if del_exp:
            print(f"  {C_GREEN}[✓] Berhasil menghapus {len(del_exp)} akun EXPIRED:{C_RESET} {', '.join(del_exp)}")
        else:
            print(f"  {C_CYAN}[i] Tidak ada akun expired yang perlu dihapus.{C_RESET}")
    if do_unused:
        if del_un:
            print(f"  {C_GREEN}[✓] Berhasil menghapus {len(del_un)} akun TIDAK DIGUNAKAN (0 B):{C_RESET} {', '.join(del_un)}")
        else:
            print(f"  {C_CYAN}[i] Tidak ada akun tidak digunakan (0 B) yang memenuhi kriteria.{C_RESET}")
    print("")
    raise SystemExit(0)

# 5. Mode --cron (Berjalan otomatis setiap menit dari /etc/cron.d/jibsz-monitor)

# 5a. Auto-Maintenance Log Size Guard (cegah disk penuh & OOM jangka panjang)
try:
    log_dir = "/var/log/jibsz"
    max_log_bytes = 10 * 1024 * 1024  # 10 MB per file
    keep_tail_bytes = 512 * 1024      # sisakan 512 KB terakhir
    if os.path.isdir(log_dir):
        for fname in os.listdir(log_dir):
            if not fname.endswith(".log"):
                continue
            fpath = os.path.join(log_dir, fname)
            try:
                if os.path.isfile(fpath) and os.path.getsize(fpath) > max_log_bytes:
                    with open(fpath, "rb") as rf:
                        rf.seek(-keep_tail_bytes, os.SEEK_END)
                        tail_data = rf.read()
                    nl_idx = tail_data.find(b"\n")
                    if nl_idx != -1:
                        tail_data = tail_data[nl_idx + 1:]
                    with open(fpath, "wb") as wf:
                        wf.write(tail_data)
            except Exception:
                pass
except Exception:
    pass

# Checkpoint SQLite WAL tiap jam (menit ke-0) agar file jibsz.db-wal tidak membengkak
try:
    if datetime.datetime.now().minute == 0:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
except Exception:
    pass
autodel_exp_cfg = web_app.get_setting("autodel_expired", "1") == "1"
autodel_unused_cfg = web_app.get_setting("autodel_unused", "0") == "1"
try:
    autodel_unused_days = float(web_app.get_setting("autodel_unused_days", "3") or 3.0)
except Exception:
    autodel_unused_days = 3.0

if autodel_exp_cfg or autodel_unused_cfg:
    conn.close()
    web_app.clean_expired_and_unused_accounts(
        clean_expired=autodel_exp_cfg,
        clean_unused=autodel_unused_cfg,
        unused_min_days=autodel_unused_days,
    )
    conn = sqlite3.connect(db_path, timeout=20.0)
    conn.row_factory = sqlite3.Row

xray_needs_reload = False
accounts = conn.execute("SELECT * FROM accounts WHERE status = 'active'").fetchall()
for acc in accounts:
    aid = acc["id"]
    uname = acc["username"]
    proto = acc["protocol"]
    exp_ts = int(acc["exp_timestamp"] or 0)
    ip_lim = max(1, int(acc["ip_limit"] or 2))
    quota_bytes = int(float(acc["quota_gb"] or 0) * (1024 ** 3))
    used_bytes = int(acc["up_bytes"] or 0) + int(acc["down_bytes"] or 0)

    active_ip_cnt = conn.execute(
        "SELECT COUNT(DISTINCT client_ip) AS cnt FROM active_ips WHERE username = ? AND protocol = ? AND last_seen >= ?",
        (uname, proto, now_ts - 45),
    ).fetchone()["cnt"]

    if now_ts >= exp_ts:
        conn.execute("UPDATE accounts SET status = 'expired' WHERE id = ?", (aid,))
        conn.execute("DELETE FROM active_ips WHERE username = ? AND protocol = ?", (uname, proto))
        if proto == "ssh":
            subprocess.run(["usermod", "-L", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["pkill", "-u", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            xray_needs_reload = True
        continue

    if active_ip_cnt > ip_lim or (quota_bytes > 0 and used_bytes >= quota_bytes):
        conn.execute("UPDATE accounts SET status = 'blocked' WHERE id = ?", (aid,))
        conn.execute("DELETE FROM active_ips WHERE username = ? AND protocol = ?", (uname, proto))
        if proto == "ssh":
            subprocess.run(["usermod", "-L", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["pkill", "-u", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            xray_needs_reload = True

conn.commit()
conn.close()

if xray_needs_reload:
    try:
        web_app.sync_xray_accounts_to_config(skip_traffic_sync=True)
    except Exception:
        pass
PYEOF
