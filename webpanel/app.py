#!/usr/bin/env python3
"""
JIBSZ TUNNELING / J1BTNL - FASTAPI WEB PANEL & AUTO-BLOCK ENFORCER V4.6 (app.py)
Author  : Jibsz Tunneling (@admj1btnl)
GitHub  : https://github.com/mohamadrojib278/jibsz-tunnel-autoinstall
Fitur   : - Auto-Block Akun Real-Time (Status -> 'blocked' / Blocked) saat:
            (1) Jumlah perangkat/IP aktif melebihi batas Devices Limit (ip_limit), ATAU
            (2) Pemakaian trafik melebihi batas Bandwidth Limit (quota_gb)
          - Begitu status menjadi 'blocked', akun otomatis dihapus dari config aktif Xray
            atau dikunci di Linux SSH (usermod -L & pkill) serta masuk ke kartu USER BLOCK / EXP
          - Dapat diaktifkan kembali kapan saja lewat tombol Edit Rules, Reset Usage, atau Renew
"""

import asyncio
import base64
import calendar
import datetime
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import struct
import subprocess
import tarfile
import threading
import time
import urllib.parse
import uuid
from typing import Optional, Dict, Any, List, Set, Tuple

import psutil
from fastapi import FastAPI, Request, Response, HTTPException, UploadFile, File, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTALL_DIR = os.environ.get("INSTALL_DIR", "/opt/jibsz-tunnel")
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/etc/jibsz")
LOG_DIR = os.environ.get("LOG_DIR", "/var/log/jibsz")
DB_PATH = os.environ.get("DB_PATH", os.path.join(CONFIG_DIR, "jibsz.db"))
LOCK_FILE = os.path.join(CONFIG_DIR, "installed.lock")
BANNER_FILE = os.path.join(CONFIG_DIR, "banner.txt")
BLOCKED_IPS_MAP_FILE = os.path.join(CONFIG_DIR, "blocked_ips.map")
BACKUP_DIR = "/root/jibsz-backups"
XRAY_CONFIG_PATH = "/usr/local/etc/xray/config.json"

LAST_LIVE_SYNC_TS = 0.0
SYNC_LIVE_LOCK = threading.Lock()
DB_SCHEMA_INITIALIZED = False


def tail_file_lines(filepath: str, max_lines: int = 120, max_bytes: int = 65536) -> List[str]:
    if not filepath or not os.path.exists(filepath):
        return []
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, max_bytes)
            if read_size <= 0:
                return []
            f.seek(size - read_size, os.SEEK_SET)
            raw = f.read(read_size)
        text = raw.decode("utf-8", errors="ignore")
        lines = text.splitlines(keepends=True)
        if size > read_size and len(lines) > 1:
            lines = lines[1:]
        return lines[-max_lines:]
    except Exception:
        return []

INMEM_PORT_IP_MAP: Dict[str, Dict[str, Any]] = {}
INMEM_PROTO_LATEST_IP: Dict[str, str] = {}
INMEM_LAST_CF_IP: Tuple[str, float] = ("", 0.0)
OVERLIMIT_BLOCKED_IPS: Dict[str, int] = {}

CF_CIDR_LIST = [
    ipaddress.ip_network("173.245.48.0/20"),
    ipaddress.ip_network("103.21.244.0/22"),
    ipaddress.ip_network("103.22.200.0/22"),
    ipaddress.ip_network("103.31.4.0/22"),
    ipaddress.ip_network("141.101.64.0/18"),
    ipaddress.ip_network("108.162.192.0/18"),
    ipaddress.ip_network("190.93.240.0/20"),
    ipaddress.ip_network("188.114.96.0/20"),
    ipaddress.ip_network("197.234.240.0/22"),
    ipaddress.ip_network("198.41.128.0/17"),
    ipaddress.ip_network("162.158.0.0/15"),
    ipaddress.ip_network("104.16.0.0/13"),
    ipaddress.ip_network("104.24.0.0/14"),
    ipaddress.ip_network("172.64.0.0/13"),
    ipaddress.ip_network("131.0.72.0/22"),
]

app = FastAPI(
    title="Jibsz Tunneling / J1BTNL Public Web Panel V4.6",
    description="Cyber-Constellation Multi-Protocol Dashboard V4.6 (@admj1btnl)",
    version="4.6.0",
)


@app.middleware("http")
async def disable_cloudflare_and_browser_cache(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


def ensure_blocked_ips_map_exists() -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if not os.path.exists(BLOCKED_IPS_MAP_FILE):
            with open(BLOCKED_IPS_MAP_FILE, "w", encoding="utf-8") as f:
                f.write("")
    except Exception:
        pass


def sync_blocked_ips_map_to_nginx() -> None:
    ensure_blocked_ips_map_exists()
    now_ts = int(time.time())
    expired_ips = [ip for ip, exp in list(OVERLIMIT_BLOCKED_IPS.items()) if now_ts >= exp]
    changed = bool(expired_ips)
    for ip in expired_ips:
        OVERLIMIT_BLOCKED_IPS.pop(ip, None)

    lines = [f"{ip} 1;\n" for ip in sorted(OVERLIMIT_BLOCKED_IPS.keys())]
    new_content = "".join(lines)
    old_content = ""
    if os.path.exists(BLOCKED_IPS_MAP_FILE):
        try:
            with open(BLOCKED_IPS_MAP_FILE, "r", encoding="utf-8") as f:
                old_content = f.read()
        except Exception:
            old_content = ""

    if new_content != old_content or changed:
        try:
            tmp_f = f"{BLOCKED_IPS_MAP_FILE}.tmp"
            with open(tmp_f, "w", encoding="utf-8") as f:
                f.write(new_content)
            os.replace(tmp_f, BLOCKED_IPS_MAP_FILE)
            subprocess.run(["nginx", "-s", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


def get_db() -> sqlite3.Connection:
    global DB_SCHEMA_INITIALIZED
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=15000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA wal_autocheckpoint=200;")
    except Exception:
        pass
    if not DB_SCHEMA_INITIALIZED:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS traffic_daily (
                    date_str TEXT PRIMARY KEY,
                    up_bytes INTEGER DEFAULT 0,
                    down_bytes INTEGER DEFAULT 0
                )
                """
            )
            conn.commit()
            DB_SCHEMA_INITIALIZED = True
        except Exception:
            pass
    return conn


def get_setting(key: str, default: str = "") -> str:
    try:
        with get_db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default
    except Exception:
        return default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

ADMIN_COOKIE_NAME = "jibsz_admin_session"
ADMIN_SESSION_TTL = 7 * 86400
DEFAULT_ADMIN_USER = "admin"
DEFAULT_ADMIN_PASS = "jibsz123"
DEFAULT_PUBLIC_IP_LIMIT = 4
DEFAULT_PUBLIC_QUOTA_GB = 0.0


def hash_admin_password(password: str, salt_hex: Optional[str] = None) -> str:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120000)
    return f"pbkdf2_sha256${salt.hex()}${dk.hex()}"


def verify_admin_password(password: str, stored_hash: str) -> bool:
    try:
        parts = (stored_hash or "").split("$")
        if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
            return False
        _, salt_hex, expected_hex = parts
        actual = hash_admin_password(password, salt_hex)
        return hmac.compare_digest(actual, stored_hash)
    except Exception:
        return False


def ensure_admin_auth_defaults() -> None:
    try:
        with get_db() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()
        if not get_setting("admin_username", ""):
            set_setting("admin_username", DEFAULT_ADMIN_USER)
        if not get_setting("admin_password_hash", ""):
            set_setting("admin_password_hash", hash_admin_password(DEFAULT_ADMIN_PASS))
        if not get_setting("admin_session_secret", ""):
            set_setting("admin_session_secret", secrets.token_hex(32))
        if not get_setting("default_ip_limit", ""):
            set_setting("default_ip_limit", str(DEFAULT_PUBLIC_IP_LIMIT))
        if not get_setting("default_quota_gb", ""):
            set_setting("default_quota_gb", str(DEFAULT_PUBLIC_QUOTA_GB))
    except Exception:
        pass


def get_public_default_limits() -> Tuple[int, float]:
    ensure_admin_auth_defaults()
    try:
        ip_lim = max(1, int(get_setting("default_ip_limit", str(DEFAULT_PUBLIC_IP_LIMIT))))
    except Exception:
        ip_lim = DEFAULT_PUBLIC_IP_LIMIT
    try:
        q_gb = max(0.0, float(get_setting("default_quota_gb", str(DEFAULT_PUBLIC_QUOTA_GB))))
    except Exception:
        q_gb = DEFAULT_PUBLIC_QUOTA_GB
    return ip_lim, q_gb


def create_admin_session_token(username: str) -> str:
    ensure_admin_auth_defaults()
    secret = get_setting("admin_session_secret", "").encode("utf-8")
    exp_ts = int(time.time()) + ADMIN_SESSION_TTL
    payload_str = f"{username}|{exp_ts}"
    sig = hmac.new(secret, payload_str.encode("utf-8"), hashlib.sha256).hexdigest()
    raw = f"{payload_str}|{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def get_authenticated_admin(request: Request) -> Optional[str]:
    ensure_admin_auth_defaults()
    token = request.cookies.get(ADMIN_COOKIE_NAME, "")
    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, exp_str, sig = raw.split("|", 2)
        exp_ts = int(exp_str)
        if int(time.time()) > exp_ts:
            return None
        current_admin = get_setting("admin_username", DEFAULT_ADMIN_USER)
        if username != current_admin:
            return None
        secret = get_setting("admin_session_secret", "").encode("utf-8")
        expected_sig = hmac.new(secret, f"{username}|{exp_ts}".encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        return username
    except Exception:
        return None


def is_admin_request(request: Request) -> bool:
    return get_authenticated_admin(request) is not None


def require_admin(request: Request) -> str:
    admin_user = get_authenticated_admin(request)
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Akses ditolak. Perubahan hanya dapat dilakukan oleh Admin.",
        )
    return admin_user


def extract_active_hostname(request: Request) -> str:
    raw_host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or ""
    ).split(",")[0].strip()
    if raw_host:
        if raw_host.startswith("["):
            bracket_end = raw_host.find("]")
            if bracket_end != -1:
                return raw_host[: bracket_end + 1]
            return raw_host
        return raw_host.split(":", 1)[0].strip()
    return get_setting("domain", "") or get_setting("public_ip", "127.0.0.1")


def is_system_installed() -> bool:
    if os.path.exists(LOCK_FILE) or get_setting("is_installed", "0") == "1":
        return True
    try:
        with get_db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM component_status "
                "WHERE component_id IN ('ssh','vless','vmess','trojan') AND status='success'"
            ).fetchone()
            if row and row["cnt"] == 4:
                set_setting("is_installed", "1")
                return True
    except Exception:
        pass
    return False


def format_bytes_human(num_bytes: float) -> str:
    b = float(max(0.0, num_bytes or 0.0))
    if b < 1024:
        return f"{int(b)} B"
    if b < 1024 ** 2:
        return f"{b / 1024:.2f} KB"
    if b < 1024 ** 3:
        return f"{b / (1024 ** 2):.2f} MB"
    if b < 1024 ** 4:
        return f"{b / (1024 ** 3):.2f} GB"
    return f"{b / (1024 ** 4):.2f} TB"


def is_cloudflare_or_local_ip(ip_str: str) -> bool:
    if not ip_str:
        return True
    ip_clean = ip_str.strip().strip("[]")
    if ip_clean in ("127.0.0.1", "::1", "0.0.0.0", "-", "localhost"):
        return True
    if ip_clean.startswith("::ffff:"):
        ip_clean = ip_clean.replace("::ffff:", "")
    try:
        ip_obj = ipaddress.ip_address(ip_clean)
        if ip_obj.is_loopback or ip_obj.is_private or ip_obj.is_unspecified:
            return True
        if isinstance(ip_obj, ipaddress.IPv4Address):
            for net in CF_CIDR_LIST:
                if ip_obj in net:
                    return True
    except Exception:
        return True
    return False


def is_valid_client_ip(ip_str: str) -> bool:
    if not ip_str:
        return False
    ip_clean = ip_str.strip().strip("[]")
    if ip_clean.startswith("::ffff:"):
        ip_clean = ip_clean.replace("::ffff:", "")
    if ip_clean in ("127.0.0.1", "::1", "0.0.0.0", "-", "localhost"):
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_clean)
        return not (ip_obj.is_loopback or ip_obj.is_unspecified)
    except Exception:
        return False


def start_inmemory_loopback_sniffer() -> None:
    def _worker():
        global INMEM_LAST_CF_IP
        try:
            raw_sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
            raw_sock.bind(("lo", 0))
        except Exception:
            return

        port_map_proto = {
            10001: "vless",
            10002: "vmess",
            10003: "trojan",
            10004: "vless",
            10005: "vmess",
            10006: "trojan",
            10015: "ssh",
        }
        while True:
            try:
                packet, _ = raw_sock.recvfrom(65535)
                if len(packet) < 54:
                    continue
                eth_proto = struct.unpack("!H", packet[12:14])[0]
                if eth_proto != 0x0800:
                    continue
                iph = struct.unpack("!BBHHHBBH4s4s", packet[14:34])
                if iph[6] != 6:
                    continue
                ihl = (iph[0] & 0xF) * 4
                tcp_start = 14 + ihl
                if len(packet) < tcp_start + 20:
                    continue
                tcph = struct.unpack("!HHLLBBHHH", packet[tcp_start:tcp_start + 20])
                src_port, dst_port = tcph[0], tcph[1]
                doff = (tcph[4] >> 4) * 4
                payload_start = tcp_start + doff
                if len(packet) <= payload_start:
                    continue
                if dst_port not in (80, 443, 10001, 10002, 10003, 10004, 10005, 10006, 10015):
                    continue
                payload = packet[payload_start:]
                if b"HTTP/1." not in payload and b"cf-connecting-ip" not in payload.lower():
                    continue

                text = payload.decode("utf-8", errors="ignore")
                real_ip = ""
                for line in text.split("\r\n"):
                    if ":" in line:
                        k, v = line.split(":", 1)
                        kl = k.strip().lower()
                        if kl in ("cf-connecting-ip", "true-client-ip", "x-real-ip", "x-forwarded-for"):
                            cand = v.strip().split(",")[0].strip()
                            if is_valid_client_ip(cand):
                                real_ip = cand
                                break

                now_f = time.time()
                if dst_port in (80, 443) and real_ip:
                    INMEM_LAST_CF_IP = (real_ip, now_f)
                    low_t = text.lower()
                    for pk in ("vless", "vmess", "trojan", "ssh"):
                        if f"/{pk}" in low_t:
                            INMEM_PROTO_LATEST_IP[pk] = real_ip
                            break

                if dst_port in port_map_proto:
                    p_name = port_map_proto[dst_port]
                    if not real_ip and (now_f - INMEM_LAST_CF_IP[1]) <= 3.0:
                        real_ip = INMEM_LAST_CF_IP[0]
                    if real_ip:
                        INMEM_PORT_IP_MAP[str(src_port)] = {
                            "ip": real_ip,
                            "proto": p_name,
                            "ts": int(now_f),
                        }
                        INMEM_PROTO_LATEST_IP[p_name] = real_ip
            except Exception:
                time.sleep(0.2)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


# ==============================================================================
# SINKRONISASI PENUH ANTARA USAGE AKUN & SERVER NETWORK USAGE (ANTI-ERROR)
# ==============================================================================
def deduct_traffic_from_daily(conn: sqlite3.Connection, deduct_up: int, deduct_dn: int) -> None:
    rem_u = max(0, int(deduct_up or 0))
    rem_d = max(0, int(deduct_dn or 0))
    if rem_u <= 0 and rem_d <= 0:
        return

    rows = conn.execute("SELECT date_str, up_bytes, down_bytes FROM traffic_daily ORDER BY date_str DESC").fetchall()
    for r in rows:
        cur_u = int(r["up_bytes"] or 0)
        cur_d = int(r["down_bytes"] or 0)
        sub_u = min(cur_u, rem_u)
        sub_d = min(cur_d, rem_d)
        new_u = cur_u - sub_u
        new_d = cur_d - sub_d
        rem_u -= sub_u
        rem_d -= sub_d
        conn.execute(
            "UPDATE traffic_daily SET up_bytes = ?, down_bytes = ? WHERE date_str = ?",
            (new_u, new_d, r["date_str"]),
        )
        if rem_u <= 0 and rem_d <= 0:
            break


def reconcile_daily_traffic_with_accounts(conn: sqlite3.Connection) -> None:
    today_str = datetime.date.today().strftime("%Y-%m-%d")
    acc_sum = conn.execute(
        "SELECT IFNULL(SUM(up_bytes), 0) AS u, IFNULL(SUM(down_bytes), 0) AS d FROM accounts"
    ).fetchone()
    acc_up = max(0, int(acc_sum["u"] or 0))
    acc_dn = max(0, int(acc_sum["d"] or 0))

    if acc_up == 0 and acc_dn == 0:
        conn.execute("DELETE FROM traffic_daily")
        conn.execute(
            "INSERT INTO traffic_daily (date_str, up_bytes, down_bytes) VALUES (?, 0, 0)",
            (today_str,),
        )
        return

    other_sum = conn.execute(
        "SELECT IFNULL(SUM(up_bytes), 0) AS u, IFNULL(SUM(down_bytes), 0) AS d FROM traffic_daily WHERE date_str != ?",
        (today_str,),
    ).fetchone()
    other_up = max(0, int(other_sum["u"] or 0))
    other_dn = max(0, int(other_sum["d"] or 0))

    if other_up > acc_up or other_dn > acc_dn:
        excess_u = max(0, other_up - acc_up)
        excess_d = max(0, other_dn - acc_dn)
        old_rows = conn.execute(
            "SELECT date_str, up_bytes, down_bytes FROM traffic_daily WHERE date_str != ? ORDER BY date_str ASC",
            (today_str,),
        ).fetchall()
        for orow in old_rows:
            cu = int(orow["up_bytes"] or 0)
            cd = int(orow["down_bytes"] or 0)
            su = min(cu, excess_u)
            sd = min(cd, excess_d)
            conn.execute(
                "UPDATE traffic_daily SET up_bytes = ?, down_bytes = ? WHERE date_str = ?",
                (cu - su, cd - sd, orow["date_str"]),
            )
            excess_u -= su
            excess_d -= sd

        other_sum2 = conn.execute(
            "SELECT IFNULL(SUM(up_bytes), 0) AS u, IFNULL(SUM(down_bytes), 0) AS d FROM traffic_daily WHERE date_str != ?",
            (today_str,),
        ).fetchone()
        other_up = max(0, int(other_sum2["u"] or 0))
        other_dn = max(0, int(other_sum2["d"] or 0))

    today_up = max(0, acc_up - other_up)
    today_dn = max(0, acc_dn - other_dn)

    conn.execute(
        """
        INSERT INTO traffic_daily (date_str, up_bytes, down_bytes)
        VALUES (?, ?, ?)
        ON CONFLICT(date_str) DO UPDATE SET
            up_bytes = excluded.up_bytes,
            down_bytes = excluded.down_bytes
        """,
        (today_str, today_up, today_dn),
    )


# ==============================================================================
# REAL-TIME LIVE CLIENT IP, TRAFFIC, & AUTO-BLOCK (DEVICE / QUOTA) ENGINE
# ==============================================================================
def get_live_kernel_socket_state() -> Tuple[Dict[str, Set[int]], List[str]]:
    proto_active_src_ports: Dict[str, Set[int]] = {
        "vless": set(),
        "vmess": set(),
        "trojan": set(),
        "ssh": set(),
    }
    direct_external_ips: List[str] = []

    port_to_proto = {
        10001: "vless",
        10004: "vless",
        10002: "vmess",
        10005: "vmess",
        10003: "trojan",
        10006: "trojan",
        10015: "ssh",
        109: "ssh",
        143: "ssh",
    }

    try:
        conns = psutil.net_connections(kind="tcp")
        for c in conns:
            if c.status != psutil.CONN_ESTABLISHED or not c.laddr or not c.raddr:
                continue
            lport = c.laddr.port
            rport = c.raddr.port
            rip = c.raddr.ip.replace("::ffff:", "")

            if lport in port_to_proto:
                p_name = port_to_proto[lport]
                proto_active_src_ports[p_name].add(rport)
                if not is_cloudflare_or_local_ip(rip) and rip not in direct_external_ips:
                    direct_external_ips.append(rip)
            elif lport in (80, 443, 22):
                if not is_cloudflare_or_local_ip(rip) and rip not in direct_external_ips:
                    direct_external_ips.append(rip)
    except Exception:
        pass

    return proto_active_src_ports, direct_external_ips


def parse_xray_log_timestamp(ts_str: str, now_ts: int) -> int:
    try:
        dt = datetime.datetime.strptime(ts_str.strip(), "%Y/%m/%d %H:%M:%S")
        ts_local = int(time.mktime(dt.timetuple()))
        ts_utc = int(calendar.timegm(dt.timetuple()))
        if abs(now_ts - ts_local) <= abs(now_ts - ts_utc):
            return ts_local
        return ts_utc
    except Exception:
        return now_ts


def sync_live_traffic_and_ips(force: bool = False) -> None:
    global LAST_LIVE_SYNC_TS
    now_f = time.time()
    if not force and (now_f - LAST_LIVE_SYNC_TS) < 3.5:
        return
    if not SYNC_LIVE_LOCK.acquire(blocking=force):
        return
    try:
        _sync_live_traffic_and_ips_locked(force=force)
    finally:
        SYNC_LIVE_LOCK.release()


def _sync_live_traffic_and_ips_locked(force: bool = False) -> None:
    global LAST_LIVE_SYNC_TS
    now_f = time.time()
    if not force and (now_f - LAST_LIVE_SYNC_TS) < 3.5:
        return
    LAST_LIVE_SYNC_TS = now_f

    nginx_log = os.path.join(LOG_DIR, "nginx_access.log")
    xray_log = os.path.join(LOG_DIR, "xray_access.log")
    ssh_ws_json = os.path.join(LOG_DIR, "ssh_ws_active.json")
    loopback_map_json = os.path.join(LOG_DIR, "xray_port_ip_map.json")
    now_ts = int(now_f)

    proto_active_src_ports, direct_external_ips = get_live_kernel_socket_state()

    port_ip_map: Dict[str, Dict[str, Any]] = dict(INMEM_PORT_IP_MAP)
    proto_latest_ip: Dict[str, str] = dict(INMEM_PROTO_LATEST_IP)

    if os.path.exists(loopback_map_json):
        try:
            with open(loopback_map_json, "r", encoding="utf-8") as f:
                lb_data = json.load(f) or {}
            for k, v in (lb_data.get("port_map", {}) or {}).items():
                if k not in port_ip_map:
                    port_ip_map[k] = v
            for pk, pv in (lb_data.get("proto_latest", {}) or {}).items():
                if isinstance(pv, dict) and is_valid_client_ip(pv.get("ip", "")):
                    proto_latest_ip.setdefault(pk, pv["ip"])
        except Exception:
            pass

    if os.path.exists(nginx_log):
        try:
            n_lines = tail_file_lines(nginx_log, max_lines=120, max_bytes=65536)
            for line in n_lines:
                parts = line.split(" ")
                if len(parts) >= 7:
                    rem_ip = parts[0].strip()
                    cf_ip = parts[2].strip()
                    xff_match = re.search(r'"([^"]+)"\s*$', line.strip())
                    xff_ip = xff_match.group(1).split(",")[0].strip() if xff_match else ""

                    cand_ip = ""
                    for test_ip in (cf_ip, xff_ip, rem_ip):
                        if is_valid_client_ip(test_ip) and not is_cloudflare_or_local_ip(test_ip):
                            cand_ip = test_ip
                            break
                    if not cand_ip:
                        for test_ip in (cf_ip, xff_ip, rem_ip):
                            if is_valid_client_ip(test_ip):
                                cand_ip = test_ip
                                break

                    if cand_ip:
                        if "/vless" in line or "vless-grpc" in line:
                            proto_latest_ip.setdefault("vless", cand_ip)
                        elif "/vmess" in line or "vmess-grpc" in line:
                            proto_latest_ip.setdefault("vmess", cand_ip)
                        elif "/trojan" in line or "trojan-grpc" in line:
                            proto_latest_ip.setdefault("trojan", cand_ip)
                        elif "/ssh-ws" in line or " 101 " in line or "/cdn-cgi/trace" in line:
                            proto_latest_ip.setdefault("ssh", cand_ip)
        except Exception:
            pass

    xray_needs_resync = False

    try:
        with get_db() as conn:
            conn.execute("DELETE FROM active_ips WHERE last_seen < ?", (now_ts - 45,))

            for x_proto in ("vless", "vmess", "trojan"):
                if not proto_active_src_ports.get(x_proto):
                    conn.execute(
                        "DELETE FROM active_ips WHERE protocol = ? AND last_seen < ?",
                        (x_proto, now_ts - 15),
                    )

            accounts = conn.execute(
                "SELECT id, username, protocol, status, ip_limit, quota_gb, up_bytes, down_bytes, exp_timestamp FROM accounts"
            ).fetchall()
            user_exact_map: Dict[str, sqlite3.Row] = {r["username"].lower(): r for r in accounts}
            active_by_proto: Dict[str, List[sqlite3.Row]] = {"vless": [], "vmess": [], "trojan": [], "ssh": []}
            for r in accounts:
                if r["status"] == "active" and r["protocol"] in active_by_proto:
                    active_by_proto[r["protocol"]].append(r)

            users_with_live_traffic: Set[str] = set()

            # 1. Tarik statistik real-time dari Xray API (127.0.0.1:10085)
            if os.path.exists("/usr/local/bin/xray"):
                try:
                    proc = subprocess.run(
                        ["/usr/local/bin/xray", "api", "statsquery", "--server=127.0.0.1:10085", "-reset=true"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    if proc.returncode == 0 and proc.stdout.strip():
                        stats_data = json.loads(proc.stdout)
                        user_credited_by_proto = {
                            "vless": {"up": 0, "dn": 0},
                            "vmess": {"up": 0, "dn": 0},
                            "trojan": {"up": 0, "dn": 0},
                        }
                        inbound_deltas = {
                            "vless": {"up": 0, "dn": 0},
                            "vmess": {"up": 0, "dn": 0},
                            "trojan": {"up": 0, "dn": 0},
                        }
                        for item in stats_data.get("stat", []):
                            name = item.get("name", "")
                            val = int(item.get("value", 0))
                            if val <= 0:
                                continue

                            m_user = re.match(r"^user>>>([^@>]+)(?:@j1btnl)?>>>traffic>>>(uplink|downlink)$", name, re.IGNORECASE)
                            if m_user:
                                raw_uname, direction = m_user.group(1), m_user.group(2).lower()
                                u_row = user_exact_map.get(raw_uname.lower())
                                if u_row:
                                    col = "up_bytes" if direction == "uplink" else "down_bytes"
                                    conn.execute(
                                        f"UPDATE accounts SET {col} = IFNULL({col}, 0) + ? WHERE id = ?",
                                        (val, u_row["id"]),
                                    )
                                    users_with_live_traffic.add(u_row["username"])
                                    p_key = u_row["protocol"]
                                    if p_key in user_credited_by_proto:
                                        d_key = "up" if direction == "uplink" else "dn"
                                        user_credited_by_proto[p_key][d_key] += val
                                continue

                            m_inb = re.match(r"^inbound>>>(vless|vmess|trojan)-(?:ws|grpc)>>>traffic>>>(uplink|downlink)$", name, re.IGNORECASE)
                            if m_inb:
                                p_name, direction = m_inb.group(1).lower(), m_inb.group(2).lower()
                                key_d = "up" if direction == "uplink" else "dn"
                                inbound_deltas[p_name][key_d] += val

                        for p_name, d_vals in inbound_deltas.items():
                            rem_up = max(0, d_vals["up"] - user_credited_by_proto[p_name]["up"])
                            rem_dn = max(0, d_vals["dn"] - user_credited_by_proto[p_name]["dn"])
                            if rem_up > 0 or rem_dn > 0:
                                u_list = active_by_proto.get(p_name, [])
                                if len(u_list) == 1:
                                    conn.execute(
                                        "UPDATE accounts SET up_bytes = IFNULL(up_bytes, 0) + ?, down_bytes = IFNULL(down_bytes, 0) + ? WHERE id = ?",
                                        (rem_up, rem_dn, u_list[0]["id"]),
                                    )
                                    users_with_live_traffic.add(u_list[0]["username"])
                except Exception:
                    pass

            # 2. Deteksi Real IP Client dari Xray Access Log + Socket Kernel + Loopback Port Map
            if os.path.exists(xray_log):
                try:
                    with open(xray_log, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(0, os.SEEK_END)
                        fsize = f.tell()
                        f.seek(max(0, fsize - 32768), os.SEEK_SET)
                        lines = f.readlines()[-180:]

                    for line in lines:
                        if "email:" not in line or "accepted" not in line:
                            continue

                        m_ts = re.match(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})", line)
                        line_ts = parse_xray_log_timestamp(m_ts.group(1), now_ts) if m_ts else now_ts

                        m_email = re.search(r"email:\s*([^\s@]+)@j1btnl", line, re.IGNORECASE)
                        if not m_email:
                            continue
                        raw_uname = m_email.group(1).strip()
                        u_row = user_exact_map.get(raw_uname.lower())
                        if not u_row or u_row["status"] != "active":
                            continue
                        uname = u_row["username"]
                        proto = u_row["protocol"]

                        m_src = re.search(
                            r"(?:from\s+)?(?:tcp:|udp:)?\[?([0-9a-fA-F\.:]+)\]?:(\d+)\s+accepted",
                            line,
                        )
                        src_ip = m_src.group(1).strip() if m_src else "127.0.0.1"
                        src_port = int(m_src.group(2)) if m_src else 0

                        is_port_established = (src_port > 0) and (src_port in proto_active_src_ports.get(proto, set()))
                        is_recent_log = abs(now_ts - line_ts) <= 45

                        if not is_port_established and not is_recent_log:
                            continue

                        resolved_ip = ""
                        if is_valid_client_ip(src_ip) and not is_cloudflare_or_local_ip(src_ip):
                            resolved_ip = src_ip
                        elif str(src_port) in port_ip_map and is_valid_client_ip(port_ip_map[str(src_port)].get("ip", "")):
                            resolved_ip = port_ip_map[str(src_port)]["ip"]
                        elif proto in proto_latest_ip and is_valid_client_ip(proto_latest_ip[proto]):
                            resolved_ip = proto_latest_ip[proto]
                        elif direct_external_ips:
                            resolved_ip = direct_external_ips[-1]

                        if is_valid_client_ip(resolved_ip):
                            seen_val = now_ts if is_port_established else line_ts
                            existing_ip = conn.execute(
                                "SELECT id FROM active_ips WHERE username = ? AND protocol = ? AND client_ip = ?",
                                (uname, proto, resolved_ip),
                            ).fetchone()
                            if existing_ip:
                                conn.execute(
                                    "UPDATE active_ips SET last_seen = ? WHERE id = ?",
                                    (seen_val, existing_ip["id"]),
                                )
                            else:
                                conn.execute(
                                    "INSERT INTO active_ips (username, protocol, client_ip, last_seen) VALUES (?, ?, ?, ?)",
                                    (uname, proto, resolved_ip, seen_val),
                                )
                except Exception:
                    pass

            for live_uname in users_with_live_traffic:
                u_row = user_exact_map.get(live_uname.lower())
                if not u_row or u_row["status"] != "active":
                    continue
                uname = u_row["username"]
                proto = u_row["protocol"]
                cur_ips = conn.execute(
                    "SELECT client_ip FROM active_ips WHERE username = ? AND protocol = ?",
                    (uname, proto),
                ).fetchall()
                if cur_ips:
                    conn.execute(
                        "UPDATE active_ips SET last_seen = ? WHERE username = ? AND protocol = ?",
                        (now_ts, uname, proto),
                    )
                else:
                    fallback_ip = proto_latest_ip.get(proto, "") or (direct_external_ips[-1] if direct_external_ips else "")
                    if is_valid_client_ip(fallback_ip):
                        conn.execute(
                            "INSERT OR REPLACE INTO active_ips (username, protocol, client_ip, last_seen) VALUES (?, ?, ?, ?)",
                            (uname, proto, fallback_ip, now_ts),
                        )

            # 3. Sinkronkan Koneksi Aktif SSH WebSocket
            conn.execute("DELETE FROM active_ips WHERE protocol = 'ssh'")
            if os.path.exists(ssh_ws_json):
                try:
                    with open(ssh_ws_json, "r", encoding="utf-8") as f:
                        ssh_conns = json.load(f) or []
                    ssh_active_rows = active_by_proto.get("ssh", [])
                    for sc in ssh_conns:
                        cip = (sc.get("client_ip") or "").strip()
                        last_s = int(sc.get("last_seen", now_ts))
                        sc_user = (sc.get("username") or "").strip()
                        if not is_valid_client_ip(cip):
                            cip = proto_latest_ip.get("ssh", "") or (direct_external_ips[-1] if direct_external_ips else "")
                        if is_valid_client_ip(cip) and (now_ts - last_s) <= 60:
                            if sc_user and sc_user.lower() in user_exact_map:
                                u_obj = user_exact_map[sc_user.lower()]
                                if u_obj["status"] == "active":
                                    conn.execute(
                                        "INSERT OR REPLACE INTO active_ips (username, protocol, client_ip, last_seen) VALUES (?, 'ssh', ?, ?)",
                                        (u_obj["username"], cip, now_ts),
                                    )
                            elif len(ssh_active_rows) == 1:
                                conn.execute(
                                    "INSERT OR REPLACE INTO active_ips (username, protocol, client_ip, last_seen) VALUES (?, 'ssh', ?, ?)",
                                    (ssh_active_rows[0]["username"], cip, now_ts),
                                )
                except Exception:
                    pass

            # 4. PENEGAKAN AUTO-BLOCK AKUN SAAT MELEBIHI BATAS DEVICE (ip_limit) ATAU BATAS KUOTA (quota_gb):
            # Ambil ulang baris accounts terbaru setelah update up_bytes/down_bytes
            fresh_accounts = conn.execute(
                "SELECT id, username, protocol, status, ip_limit, quota_gb, up_bytes, down_bytes FROM accounts WHERE status = 'active'"
            ).fetchall()

            for acc in fresh_accounts:
                aid = acc["id"]
                uname = acc["username"]
                proto = acc["protocol"]
                ip_lim = max(1, int(acc["ip_limit"] or 2))
                quota_gb = float(acc["quota_gb"] or 0.0)
                quota_bytes = int(quota_gb * (1024 ** 3)) if quota_gb > 0 else 0
                used_bytes = int(acc["up_bytes"] or 0) + int(acc["down_bytes"] or 0)

                user_ip_rows = conn.execute(
                    "SELECT id, client_ip FROM active_ips WHERE username = ? AND protocol = ? AND last_seen >= ? ORDER BY id ASC",
                    (uname, proto, now_ts - 45),
                ).fetchall()

                exceeded_device_limit = len(user_ip_rows) > ip_lim
                exceeded_quota_limit = (quota_bytes > 0) and (used_bytes >= quota_bytes)

                if exceeded_device_limit or exceeded_quota_limit:
                    # Ubah status akun menjadi 'blocked' (Blocked)
                    conn.execute("UPDATE accounts SET status = 'blocked' WHERE id = ?", (aid,))
                    conn.execute("DELETE FROM active_ips WHERE username = ? AND protocol = ?", (uname, proto))

                    for ir in user_ip_rows[ip_lim:]:
                        if is_valid_client_ip(ir["client_ip"]):
                            OVERLIMIT_BLOCKED_IPS[ir["client_ip"]] = now_ts + 180

                    if proto == "ssh":
                        subprocess.run(["usermod", "-L", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        subprocess.run(["pkill", "-u", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        xray_needs_resync = True

            # 5. Sinkronkan tabel traffic_daily dengan total akumulasi seluruh akun
            reconcile_daily_traffic_with_accounts(conn)
            conn.commit()
    except Exception:
        pass

    sync_blocked_ips_map_to_nginx()
    if xray_needs_resync:
        sync_xray_accounts_to_config(skip_traffic_sync=True)


def get_synchronized_network_usage() -> Dict[str, Any]:
    now_dt = datetime.date.today()
    today_str = now_dt.strftime("%Y-%m-%d")
    month_prefix = now_dt.strftime("%Y-%m-")
    year_prefix = now_dt.strftime("%Y-")

    with get_db() as conn:
        reconcile_daily_traffic_with_accounts(conn)
        conn.commit()

        d_row = conn.execute(
            "SELECT IFNULL(SUM(up_bytes),0) AS u, IFNULL(SUM(down_bytes),0) AS d FROM traffic_daily WHERE date_str = ?",
            (today_str,),
        ).fetchone()
        m_row = conn.execute(
            "SELECT IFNULL(SUM(up_bytes),0) AS u, IFNULL(SUM(down_bytes),0) AS d FROM traffic_daily WHERE date_str LIKE ?",
            (month_prefix + "%",),
        ).fetchone()
        y_row = conn.execute(
            "SELECT IFNULL(SUM(up_bytes),0) AS u, IFNULL(SUM(down_bytes),0) AS d FROM traffic_daily WHERE date_str LIKE ?",
            (year_prefix + "%",),
        ).fetchone()
        all_row = conn.execute(
            "SELECT IFNULL(SUM(up_bytes),0) AS u, IFNULL(SUM(down_bytes),0) AS d FROM accounts"
        ).fetchone()

    all_tx, all_rx = max(0, int(all_row["u"] or 0)), max(0, int(all_row["d"] or 0))
    day_tx, day_rx = min(all_tx, max(0, int(d_row["u"] or 0))), min(all_rx, max(0, int(d_row["d"] or 0)))
    mo_tx, mo_rx = min(all_tx, max(day_tx, int(m_row["u"] or 0))), min(all_rx, max(day_rx, int(m_row["d"] or 0)))
    yr_tx, yr_rx = min(all_tx, max(mo_tx, int(y_row["u"] or 0))), min(all_rx, max(mo_rx, int(y_row["d"] or 0)))

    def pack_period(rx_val: int, tx_val: int) -> Dict[str, str]:
        return {
            "total": format_bytes_human(rx_val + tx_val),
            "up": format_bytes_human(tx_val),
            "down": format_bytes_human(rx_val),
        }

    return {
        "download": format_bytes_human(all_rx),
        "upload": format_bytes_human(all_tx),
        "total": format_bytes_human(all_rx + all_tx),
        "today": pack_period(day_rx, day_tx),
        "month": pack_period(mo_rx, mo_tx),
        "year": pack_period(yr_rx, yr_tx),
        "all_time": pack_period(all_rx, all_tx),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ==============================================================================
# ENGINE AUTO-DELETE AKUN EXPIRED & AKUN TIDAK DIGUNAKAN (IDLE / UNUSED)
# ==============================================================================
def clean_expired_and_unused_accounts(
    clean_expired: bool = True,
    clean_unused: bool = False,
    unused_min_days: float = 0.0,
) -> Dict[str, List[str]]:
    sync_live_traffic_and_ips(force=True)
    now_ts = int(time.time())
    deleted_expired: List[str] = []
    deleted_unused: List[str] = []
    xray_changed = False

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id ASC").fetchall()
        online_rows = conn.execute(
            "SELECT DISTINCT username, protocol FROM active_ips WHERE last_seen >= ?",
            (now_ts - 60,),
        ).fetchall()
        online_keys = {f"{r['protocol']}:{r['username'].lower()}" for r in online_rows}

        for r in rows:
            aid = r["id"]
            uname = r["username"]
            proto = r["protocol"]
            exp_ts = int(r["exp_timestamp"] or 0)
            st = (r["status"] or "active").lower()
            up_b = int(r["up_bytes"] or 0)
            dn_b = int(r["down_bytes"] or 0)
            tot_b = up_b + dn_b
            is_online = f"{proto}:{uname.lower()}" in online_keys

            age_days = 999.0
            created_str = (r["created_at"] or "").strip()
            if created_str:
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                    try:
                        c_dt = datetime.datetime.strptime(created_str, fmt)
                        c_ts = int(calendar.timegm(c_dt.timetuple()))
                        age_days = max(0.0, (now_ts - c_ts) / 86400.0)
                        break
                    except Exception:
                        pass

            is_exp = (now_ts >= exp_ts) or (st == "expired")
            is_unused = (tot_b <= 0) and (not is_online) and (age_days >= float(unused_min_days))

            should_del_exp = clean_expired and is_exp
            should_del_unused = clean_unused and (not should_del_exp) and is_unused

            if should_del_exp or should_del_unused:
                deduct_traffic_from_daily(conn, up_b, dn_b)
                conn.execute("DELETE FROM accounts WHERE id = ?", (aid,))
                conn.execute("DELETE FROM active_ips WHERE username = ? AND protocol = ?", (uname, proto))
                if proto == "ssh":
                    subprocess.run(["pkill", "-u", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["userdel", "-f", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    xray_changed = True

                label = f"{uname} ({proto.upper()})"
                if should_del_exp:
                    deleted_expired.append(label)
                else:
                    deleted_unused.append(label)

        reconcile_daily_traffic_with_accounts(conn)
        conn.commit()

    if xray_changed:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {
        "deleted_expired": deleted_expired,
        "deleted_unused": deleted_unused,
    }


# ==============================================================================
# PYDANTIC SCHEMAS
# ==============================================================================
class InstallStartRequest(BaseModel):
    mode: str
    domain: str
    email: Optional[str] = "admin@j1btnl.local"
    cf_token: Optional[str] = ""


class RetryComponentRequest(BaseModel):
    component_id: str


class ServiceActionRequest(BaseModel):
    service: str


class CreateAccountRequest(BaseModel):
    protocol: str
    username: str
    secret: Optional[str] = ""
    duration_mode: str = "days"
    duration_value: int = 30
    ip_limit: int = 2
    quota_gb: float = 50.0


class RenewAccountRequest(BaseModel):
    add_days: int = 30
    quota_gb: Optional[float] = None
    ip_limit: Optional[int] = None


class EditRulesRequest(BaseModel):
    ip_limit: int = 2
    quota_gb: float = 0.0


class UnlockAccountRequest(BaseModel):
    ip_limit: Optional[int] = None


class BannerUpdateRequest(BaseModel):
    banner_html: str


class RestoreLocalRequest(BaseModel):
    filename: str


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminChangePasswordRequest(BaseModel):
    current_password: str
    new_username: Optional[str] = None
    new_password: str


class PublicDefaultLimitsRequest(BaseModel):
    default_ip_limit: int = 4
    default_quota_gb: float = 0.0


# ==============================================================================
# HELPER PROVISIONING AKUN & SINKRONISASI XRAY V1.8.24 LTS
# ==============================================================================
def sync_xray_accounts_to_config(skip_traffic_sync: bool = False) -> None:
    if not skip_traffic_sync:
        try:
            sync_live_traffic_and_ips(force=True)
        except Exception:
            pass

    template_path = os.path.join(INSTALL_DIR, "backend", "xray_config.json")
    os.makedirs(os.path.dirname(XRAY_CONFIG_PATH), exist_ok=True)

    if os.path.exists(template_path):
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    elif os.path.exists(XRAY_CONFIG_PATH):
        try:
            with open(XRAY_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    else:
        return

    with get_db() as conn:
        rows = conn.execute(
            "SELECT protocol, username, secret FROM accounts WHERE status = 'active' AND protocol IN ('vless','vmess','trojan')"
        ).fetchall()

    vless_clients = []
    vmess_clients = []
    trojan_clients = []

    for r in rows:
        sec = (r["secret"] or "").strip()
        if not sec:
            continue
        email_tag = f"{r['username']}@j1btnl"
        if r["protocol"] == "vless":
            vless_clients.append({"id": sec, "email": email_tag, "level": 0})
        elif r["protocol"] == "vmess":
            vmess_clients.append({"id": sec, "alterId": 0, "email": email_tag, "level": 0})
        elif r["protocol"] == "trojan":
            trojan_clients.append({"password": sec, "email": email_tag, "level": 0})

    for inbound in cfg.get("inbounds", []):
        tag = inbound.get("tag", "")
        if tag in ("vless-ws", "vless-grpc"):
            inbound.setdefault("settings", {})["clients"] = vless_clients
        elif tag in ("vmess-ws", "vmess-grpc"):
            inbound.setdefault("settings", {})["clients"] = vmess_clients
        elif tag in ("trojan-ws", "trojan-grpc"):
            inbound.setdefault("settings", {})["clients"] = trojan_clients

    tmp_cfg = f"{XRAY_CONFIG_PATH}.tmp"
    with open(tmp_cfg, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp_cfg, 0o644)
    os.replace(tmp_cfg, XRAY_CONFIG_PATH)

    os.makedirs(LOG_DIR, exist_ok=True)
    for lf in ("xray_access.log", "xray_error.log"):
        lp = os.path.join(LOG_DIR, lf)
        if not os.path.exists(lp):
            open(lp, "a").close()
        try:
            os.chmod(lp, 0o666)
        except Exception:
            pass

    shutil.rmtree("/etc/systemd/system/xray.service.d", ignore_errors=True)
    subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "restart", "xray"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    chk = subprocess.run(["pgrep", "-x", "xray"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if chk.returncode != 0 and os.path.exists("/usr/local/bin/xray"):
        subprocess.Popen(
            ["/usr/local/bin/xray", "run", "-config", XRAY_CONFIG_PATH],
            stdout=open(os.path.join(LOG_DIR, "xray_error.log"), "a"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def ensure_ssl_cert_exists(domain: str) -> None:
    ssl_dir = os.path.join(CONFIG_DIR, "ssl")
    os.makedirs(ssl_dir, exist_ok=True)
    fullchain = os.path.join(ssl_dir, "fullchain.pem")
    privkey = os.path.join(ssl_dir, "privkey.pem")
    if not os.path.exists(fullchain) or not os.path.exists(privkey):
        cn = domain if domain and domain != "localhost" else "jibsz.tunnel.local"
        subprocess.run(
            [
                "openssl", "req", "-x509", "-nodes", "-days", "3650",
                "-newkey", "rsa:2048",
                "-keyout", privkey,
                "-out", fullchain,
                "-subj", f"/C=ID/ST=EastJava/L=Trenggalek/O=JibszTunneling/OU=J1BTNL/CN={cn}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def sync_nginx_config_if_installed() -> None:
    ensure_blocked_ips_map_exists()
    domain = get_setting("domain", "").strip()
    if not domain and not is_system_installed():
        return
    if not domain:
        domain = "localhost"
    mode = get_setting("install_mode", "public")
    ensure_ssl_cert_exists(domain)

    tpl_name = "nginx_nat.conf" if mode == "nat" else "nginx_public.conf"
    tpl_path = os.path.join(INSTALL_DIR, "backend", tpl_name)
    target_conf = "/etc/nginx/conf.d/jibsz_tunnel.conf"

    if os.path.exists(tpl_path) and os.path.exists("/etc/nginx/conf.d"):
        try:
            if os.path.exists("/etc/nginx/sites-enabled"):
                for fn in os.listdir("/etc/nginx/sites-enabled"):
                    try:
                        os.remove(os.path.join("/etc/nginx/sites-enabled", fn))
                    except Exception:
                        pass
            for fn in os.listdir("/etc/nginx/conf.d"):
                if fn.endswith(".conf") and fn != "jibsz_tunnel.conf":
                    try:
                        os.remove(os.path.join("/etc/nginx/conf.d", fn))
                    except Exception:
                        pass

            with open(tpl_path, "r", encoding="utf-8") as f:
                content = f.read().replace("__DOMAIN__", domain)
            with open(target_conf, "w", encoding="utf-8") as f:
                f.write(content)
            subprocess.run(["systemctl", "restart", "nginx"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass


async def background_live_enforcer_loop() -> None:
    while True:
        await asyncio.sleep(5)
        try:
            if os.path.exists(DB_PATH):
                await asyncio.to_thread(sync_live_traffic_and_ips)
        except Exception:
            pass


@app.on_event("startup")
async def on_startup_sync():
    ensure_blocked_ips_map_exists()
    ensure_admin_auth_defaults()
    asyncio.create_task(background_live_enforcer_loop())
    try:
        if os.path.exists(DB_PATH):
            sync_nginx_config_if_installed()
            sync_xray_accounts_to_config()
    except Exception:
        pass


def provision_linux_ssh_user(username: str, password: str, exp_date_str: str) -> None:
    check = subprocess.run(["id", "-u", username], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if check.returncode != 0:
        subprocess.run(
            ["useradd", "-e", exp_date_str, "-s", "/bin/false", "-M", username],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.run(
            ["usermod", "-e", exp_date_str, username],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    chpasswd_proc = subprocess.Popen(["chpasswd"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chpasswd_proc.communicate(input=f"{username}:{password}".encode("utf-8"))


def lock_unlock_linux_ssh_user(username: str, lock: bool) -> None:
    flag = "-L" if lock else "-U"
    subprocess.run(["usermod", flag, username], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if lock:
        subprocess.run(["pkill", "-u", username], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sync_ssh_accounts_to_linux() -> int:
    synced = 0
    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT username, secret, exp_timestamp, status FROM accounts WHERE protocol = 'ssh'"
            ).fetchall()
        for r in rows:
            uname = (r["username"] or "").strip()
            secret = (r["secret"] or "").strip()
            if not uname or not secret:
                continue
            exp_ts = int(r["exp_timestamp"] or int(time.time()))
            exp_dt = datetime.datetime.fromtimestamp(exp_ts)
            exp_date_linux = (exp_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            provision_linux_ssh_user(uname, secret, exp_date_linux)
            is_active = (r["status"] or "active").lower() == "active"
            lock_unlock_linux_ssh_user(uname, lock=(not is_active))
            synced += 1
    except Exception:
        pass
    return synced


def generate_qr_svg_data_uri(data_text: str) -> str:
    if not HAS_QRCODE or not data_text:
        return ""
    try:
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(data_text)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix)
        scale = 6
        dim = size * scale
        svg_parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" width="{dim}" height="{dim}" shape-rendering="crispEdges">',
            f'<rect width="{dim}" height="{dim}" fill="#ffffff"/>',
        ]
        for r_idx, row in enumerate(matrix):
            for c_idx, val in enumerate(row):
                if val:
                    svg_parts.append(
                        f'<rect x="{c_idx * scale}" y="{r_idx * scale}" width="{scale}" height="{scale}" fill="#000000"/>'
                    )
        svg_parts.append("</svg>")
        svg_bytes = "".join(svg_parts).encode("utf-8")
        return "data:image/svg+xml;base64," + base64.b64encode(svg_bytes).decode("ascii")
    except Exception:
        return ""


def build_account_connection_details(account: Dict[str, Any]) -> Dict[str, Any]:
    domain = get_setting("domain", "localhost") or get_setting("public_ip", "127.0.0.1")
    pub_ip = get_setting("public_ip", "127.0.0.1")
    mode = get_setting("install_mode", "public")
    proto = account["protocol"]
    user = account["username"]
    secret = account["secret"]

    details: Dict[str, Any] = {
        "host": domain,
        "ip_public": pub_ip,
        "mode": "VPS NAT + CF Tunnel" if mode == "nat" else "VPS IP Public",
        "port_tls": 443,
        "port_ntls": 80,
        "path_ws": f"/{proto}" if proto != "ssh" else "/ssh-ws (atau /)",
        "service_name_grpc": f"{proto}-grpc" if proto != "ssh" else "-",
        "ssh_format_tls": "",
        "ssh_format_ntls": "",
        "ssh_payload_ws": "",
        "ssh_payload_trace": "",
        "uri_tls": "",
        "uri_ntls": "",
        "uri_grpc": "",
        "qr_svg": "",
    }

    if proto == "ssh":
        details["ssh_format_tls"] = f"{domain}:443@{user}:{secret}"
        details["ssh_format_ntls"] = f"{domain}:80@{user}:{secret}"
        details["ssh_payload_ws"] = (
            f"GET /ssh-ws HTTP/1.1[crlf]"
            f"Host: {domain}[crlf]"
            f"Upgrade: websocket[crlf]"
            f"Connection: Upgrade[crlf]"
            f"User-Agent: [ua][crlf][crlf]"
        )
        details["ssh_payload_trace"] = (
            "GET /cdn-cgi/trace HTTP/1.1[crlf]Host: [host][crlf][crlf]"
            "GET-WSS / HTTP/1.1[crlf]Host: [host][crlf]User-Agent: [ua][crlf]"
            "Upgrade: Websocket[crlf][crlf]"
        )
        details["uri_tls"] = details["ssh_format_tls"]
        details["uri_ntls"] = details["ssh_format_ntls"]
        details["qr_svg"] = generate_qr_svg_data_uri(details["ssh_format_tls"])
        return details

    if proto == "vmess":
        vmess_tls_obj = {
            "v": "2",
            "ps": f"{user}-VMESS-TLS",
            "add": domain,
            "port": "443",
            "id": secret,
            "aid": "0",
            "scy": "auto",
            "net": "ws",
            "type": "none",
            "host": domain,
            "path": "/vmess",
            "tls": "tls",
            "sni": domain,
            "alpn": "http/1.1",
        }
        vmess_ntls_obj = {
            "v": "2",
            "ps": f"{user}-VMESS-NTLS",
            "add": domain,
            "port": "80",
            "id": secret,
            "aid": "0",
            "scy": "auto",
            "net": "ws",
            "type": "none",
            "host": domain,
            "path": "/vmess",
            "tls": "none",
        }
        vmess_grpc_obj = {
            "v": "2",
            "ps": f"{user}-VMESS-gRPC",
            "add": domain,
            "port": "443",
            "id": secret,
            "aid": "0",
            "scy": "auto",
            "net": "grpc",
            "type": "gun",
            "host": domain,
            "path": "vmess-grpc",
            "tls": "tls",
            "sni": domain,
        }
        details["uri_tls"] = "vmess://" + base64.b64encode(json.dumps(vmess_tls_obj).encode("utf-8")).decode("ascii")
        details["uri_ntls"] = "vmess://" + base64.b64encode(json.dumps(vmess_ntls_obj).encode("utf-8")).decode("ascii")
        details["uri_grpc"] = "vmess://" + base64.b64encode(json.dumps(vmess_grpc_obj).encode("utf-8")).decode("ascii")
    elif proto == "trojan":
        details["uri_tls"] = (
            f"trojan://{secret}@{domain}:443?path=%2Ftrojan&security=tls"
            f"&host={domain}&type=ws&sni={domain}&alpn=http%2F1.1#{urllib.parse.quote(user + '-TROJAN-TLS')}"
        )
        details["uri_ntls"] = (
            f"trojan://{secret}@{domain}:80?path=%2Ftrojan&security=none"
            f"&host={domain}&type=ws#{urllib.parse.quote(user + '-TROJAN-NTLS')}"
        )
        details["uri_grpc"] = (
            f"trojan://{secret}@{domain}:443?mode=gun&security=tls"
            f"&type=grpc&serviceName=trojan-grpc&sni={domain}#{urllib.parse.quote(user + '-TROJAN-gRPC')}"
        )
    else:
        details["uri_tls"] = (
            f"vless://{secret}@{domain}:443?path=%2Fvless&security=tls&encryption=none"
            f"&host={domain}&type=ws&sni={domain}&alpn=http%2F1.1#{urllib.parse.quote(user + '-VLESS-TLS')}"
        )
        details["uri_ntls"] = (
            f"vless://{secret}@{domain}:80?path=%2Fvless&security=none&encryption=none"
            f"&host={domain}&type=ws#{urllib.parse.quote(user + '-VLESS-NTLS')}"
        )
        details["uri_grpc"] = (
            f"vless://{secret}@{domain}:443?mode=gun&security=tls&encryption=none"
            f"&type=grpc&serviceName=vless-grpc&sni={domain}#{urllib.parse.quote(user + '-VLESS-gRPC')}"
        )

    details["qr_svg"] = generate_qr_svg_data_uri(details["uri_tls"])
    return details


@app.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    ensure_admin_auth_defaults()
    active_host = extract_active_hostname(request)
    admin_user = get_authenticated_admin(request)
    def_ip, def_quota = get_public_default_limits()
    if is_system_installed():
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "domain": get_setting("domain", "Belum diatur"),
                "active_host": active_host,
                "is_admin": admin_user is not None,
                "admin_username": admin_user or "",
                "default_ip_limit": def_ip,
                "default_quota_gb": def_quota,
                "install_mode": get_setting("install_mode", "public"),
                "panel_port": get_setting("panel_port", "8080"),
                "public_ip": get_setting("public_ip", "127.0.0.1"),
                "cache_v": str(int(time.time())),
            },
        )
    return templates.TemplateResponse(
        request=request,
        name="install.html",
        context={
            "domain": get_setting("domain", ""),
            "acme_email": get_setting("acme_email", "admin@j1btnl.local"),
            "cf_token": get_setting("cf_token", ""),
            "install_mode": get_setting("install_mode", "public"),
            "panel_port": get_setting("panel_port", "8080"),
            "public_ip": get_setting("public_ip", "127.0.0.1"),
            "cache_v": str(int(time.time())),
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def force_dashboard(request: Request):
    if not is_system_installed():
        return RedirectResponse(url="/")
    ensure_admin_auth_defaults()
    active_host = extract_active_hostname(request)
    admin_user = get_authenticated_admin(request)
    def_ip, def_quota = get_public_default_limits()
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "domain": get_setting("domain", "Belum diatur"),
            "active_host": active_host,
            "is_admin": admin_user is not None,
            "admin_username": admin_user or "",
            "default_ip_limit": def_ip,
            "default_quota_gb": def_quota,
            "install_mode": get_setting("install_mode", "public"),
            "panel_port": get_setting("panel_port", "8080"),
            "public_ip": get_setting("public_ip", "127.0.0.1"),
            "cache_v": str(int(time.time())),
        },
    )


@app.get("/api/auth/me")
async def auth_me(request: Request):
    ensure_admin_auth_defaults()
    admin_user = get_authenticated_admin(request)
    def_ip, def_quota = get_public_default_limits()
    return {
        "is_admin": admin_user is not None,
        "username": admin_user,
        "default_ip_limit": def_ip,
        "default_quota_gb": def_quota,
    }


@app.post("/api/auth/login")
async def auth_login(payload: AdminLoginRequest, response: Response):
    ensure_admin_auth_defaults()
    stored_user = get_setting("admin_username", DEFAULT_ADMIN_USER)
    stored_hash = get_setting("admin_password_hash", "")
    if payload.username.strip() != stored_user or not verify_admin_password(payload.password, stored_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Username atau password admin salah.",
        )
    token = create_admin_session_token(stored_user)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    def_ip, def_quota = get_public_default_limits()
    return {
        "status": "ok",
        "is_admin": True,
        "username": stored_user,
        "default_ip_limit": def_ip,
        "default_quota_gb": def_quota,
    }


@app.post("/api/auth/logout")
async def auth_logout(response: Response):
    response.delete_cookie(key=ADMIN_COOKIE_NAME, path="/")
    return {"status": "ok", "is_admin": False}


@app.post("/api/auth/change-password")
async def auth_change_password(request: Request, payload: AdminChangePasswordRequest, response: Response):
    require_admin(request)
    stored_user = get_setting("admin_username", DEFAULT_ADMIN_USER)
    stored_hash = get_setting("admin_password_hash", "")
    if not verify_admin_password(payload.current_password, stored_hash):
        raise HTTPException(status_code=400, detail="Password admin saat ini salah.")
    new_pass = (payload.new_password or "").strip()
    if len(new_pass) < 4:
        raise HTTPException(status_code=400, detail="Password baru minimal 4 karakter.")
    new_user = (payload.new_username or stored_user).strip()
    if not new_user:
        new_user = stored_user

    set_setting("admin_username", new_user)
    set_setting("admin_password_hash", hash_admin_password(new_pass))
    set_setting("admin_session_secret", secrets.token_hex(32))

    token = create_admin_session_token(new_user)
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        max_age=ADMIN_SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    return {"status": "updated", "username": new_user}


@app.post("/api/settings/public-limits")
async def update_public_default_limits(request: Request, payload: PublicDefaultLimitsRequest):
    require_admin(request)
    ip_lim = max(1, int(payload.default_ip_limit))
    q_gb = max(0.0, float(payload.default_quota_gb))
    set_setting("default_ip_limit", str(ip_lim))
    set_setting("default_quota_gb", str(q_gb))
    return {"status": "saved", "default_ip_limit": ip_lim, "default_quota_gb": q_gb}


@app.post("/api/install/start")
async def start_installation(payload: InstallStartRequest):
    if is_system_installed():
        raise HTTPException(status_code=403, detail="Instalasi sudah selesai. Wizard telah dinonaktifkan.")
    mode = payload.mode.lower().strip()
    if mode not in ("public", "nat"):
        raise HTTPException(status_code=400, detail="Mode harus 'public' atau 'nat'.")
    domain = payload.domain.strip()
    if not domain:
        raise HTTPException(status_code=400, detail="Domain / Subdomain wajib diisi.")
    if mode == "nat" and not (payload.cf_token or "").strip():
        raise HTTPException(status_code=400, detail="Cloudflare Tunnel Token wajib diisi untuk mode VPS NAT.")

    set_setting("install_mode", mode)
    set_setting("domain", domain)
    set_setting("acme_email", (payload.email or "admin@j1btnl.local").strip())
    if mode == "nat":
        set_setting("cf_token", (payload.cf_token or "").strip())

    with get_db() as conn:
        conn.execute("UPDATE component_status SET status='pending', message='Dalam antrean instalasi...'")
        conn.commit()

    engine_script = os.path.join(INSTALL_DIR, "backend", "installer_engine.sh")
    subprocess.Popen(
        ["/bin/bash", engine_script, "--all"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"status": "started", "mode": mode, "domain": domain}


@app.post("/api/install/retry")
async def retry_component(request: Request, payload: RetryComponentRequest):
    if is_system_installed():
        require_admin(request)
    comp = payload.component_id.strip()
    engine_script = os.path.join(INSTALL_DIR, "backend", "installer_engine.sh")

    if comp == "all_failed":
        subprocess.Popen(
            ["/bin/bash", engine_script, "--retry-failed"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    else:
        with get_db() as conn:
            conn.execute(
                "UPDATE component_status SET status='installing', message='Mengulangi instalasi komponen...' WHERE component_id=?",
                (comp,),
            )
            conn.commit()
        subprocess.Popen(
            ["/bin/bash", engine_script, "--component", comp],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return {"status": "retrying", "component_id": comp}


@app.get("/api/install/status")
async def get_installation_status():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT component_id, name, status, message, updated_at FROM component_status "
            "ORDER BY CASE component_id "
            "WHEN 'nginx' THEN 1 WHEN 'tunnel_ssl' THEN 2 WHEN 'ssh' THEN 3 "
            "WHEN 'vless' THEN 4 WHEN 'vmess' THEN 5 WHEN 'trojan' THEN 6 ELSE 7 END"
        ).fetchall()
        components = [dict(r) for r in rows]

    log_file = os.path.join(LOG_DIR, "install.log")
    logs = ""
    if os.path.exists(log_file):
        logs = "".join(tail_file_lines(log_file, max_lines=80, max_bytes=65536))

    return {
        "is_installed": is_system_installed(),
        "mode": get_setting("install_mode", "public"),
        "domain": get_setting("domain", ""),
        "components": components,
        "logs": logs,
    }


def check_service_running(svc_name: str) -> bool:
    proc_map = {
        "nginx": "nginx",
        "xray": "xray",
        "ssh": "sshd",
        "dropbear": "dropbear",
        "ssh-ws": "ssh_ws_proxy.py",
        "cloudflared": "cloudflared",
        "cron": "cron",
    }
    res = subprocess.run(
        ["systemctl", "is-active", "--quiet", svc_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if res.returncode == 0:
        return True
    pattern = proc_map.get(svc_name, svc_name)
    pgrep_res = subprocess.run(["pgrep", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return pgrep_res.returncode == 0


def get_os_pretty_name() -> str:
    if os.path.exists("/etc/os-release"):
        with open("/etc/os-release", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    return f"{platform.system()} {platform.release()}"


@app.get("/api/system/overview")
async def system_overview():
    await asyncio.to_thread(sync_live_traffic_and_ips)

    boot_ts = psutil.boot_time()
    uptime_sec = int(time.time() - boot_ts)
    days, rem = divmod(uptime_sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    uptime_str = f"{days}d {hours}h {minutes}m"

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    cpu_pct = psutil.cpu_percent(interval=None)
    try:
        load1, load5, load15 = os.getloadavg()
    except Exception:
        load1, load5, load15 = 0.0, 0.0, 0.0

    mode = get_setting("install_mode", "public")
    service_list = ["nginx", "xray", "ssh", "dropbear", "ssh-ws", "cron"]
    if mode == "nat":
        service_list.insert(5, "cloudflared")

    services = [
        {
            "name": svc,
            "status": "running" if check_service_running(svc) else "stopped",
        }
        for svc in service_list
    ]

    now_cutoff = int(time.time()) - 45
    with get_db() as conn:
        counts = {"ssh": 0, "vmess": 0, "vless": 0, "trojan": 0}
        for row in conn.execute("SELECT protocol, COUNT(*) AS cnt FROM accounts WHERE status='active' GROUP BY protocol"):
            if row["protocol"] in counts:
                counts[row["protocol"]] = row["cnt"]

        all_rows = conn.execute("SELECT username, protocol, status FROM accounts").fetchall()
        online_rows = conn.execute(
            "SELECT username, protocol, COUNT(DISTINCT client_ip) AS ip_cnt FROM active_ips WHERE last_seen >= ? GROUP BY username, protocol",
            (now_cutoff,),
        ).fetchall()
        online_ips = conn.execute(
            "SELECT COUNT(DISTINCT client_ip) AS cnt FROM active_ips WHERE last_seen >= ?",
            (now_cutoff,),
        ).fetchone()["cnt"]

    online_user_keys = {f"{r['protocol']}:{r['username'].lower()}" for r in online_rows if r["ip_cnt"] > 0}

    total_ssh = sum(1 for r in all_rows if r["protocol"] == "ssh")
    total_xray = sum(1 for r in all_rows if r["protocol"] != "ssh")
    total_all = len(all_rows)

    online_ssh = sum(
        1 for r in all_rows if r["protocol"] == "ssh" and r["status"] == "active" and f"ssh:{r['username'].lower()}" in online_user_keys
    )
    online_xray = sum(
        1 for r in all_rows if r["protocol"] != "ssh" and r["status"] == "active" and f"{r['protocol']}:{r['username'].lower()}" in online_user_keys
    )
    online_users_total = online_ssh + online_xray

    blocked_ssh = sum(1 for r in all_rows if r["protocol"] == "ssh" and r["status"] != "active")
    blocked_xray = sum(1 for r in all_rows if r["protocol"] != "ssh" and r["status"] != "active")
    blocked_total = blocked_ssh + blocked_xray

    return {
        "system": {
            "os": get_os_pretty_name(),
            "kernel": platform.release(),
            "arch": platform.machine(),
            "hostname": platform.node(),
            "uptime": uptime_str,
            "cpu_percent": cpu_pct,
            "cpu_cores": psutil.cpu_count(logical=True) or 1,
            "load_avg": f"{load1:.2f} / {load5:.2f} / {load15:.2f}",
            "ram_used_mb": int(mem.used / (1024**2)),
            "ram_total_mb": int(mem.total / (1024**2)),
            "ram_percent": mem.percent,
            "disk_used_gb": round(disk.used / (1024**3), 2),
            "disk_total_gb": round(disk.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "bandwidth": get_synchronized_network_usage(),
        },
        "network": {
            "public_ip": get_setting("public_ip", "127.0.0.1"),
            "mode": "VPS NAT + CF Tunnel" if mode == "nat" else "VPS IP Public",
            "mode_code": mode,
            "domain": get_setting("domain", "localhost"),
            "panel_port": get_setting("panel_port", "8080"),
        },
        "services": services,
        "account_counts": counts,
        "total_accounts": total_all,
        "online_ips": online_ips,
        "user_statistics": {
            "total_users": total_all,
            "total_ssh": total_ssh,
            "total_xray": total_xray,
            "online_users": online_users_total,
            "online_ssh": online_ssh,
            "online_xray": online_xray,
            "blocked_users": blocked_total,
            "blocked_ssh": blocked_ssh,
            "blocked_xray": blocked_xray,
        },
    }


@app.post("/api/service/restart")
async def restart_service(request: Request, payload: ServiceActionRequest):
    require_admin(request)
    allowed = {"nginx", "xray", "ssh", "dropbear", "ssh-ws", "cloudflared", "cron", "badvpn-udpgw"}
    svc = payload.service.strip()
    if svc not in allowed:
        raise HTTPException(status_code=400, detail="Service tidak dikenal.")
    if svc == "xray":
        sync_xray_accounts_to_config()
    elif svc == "nginx":
        sync_nginx_config_if_installed()
    else:
        subprocess.run(["systemctl", "restart", svc], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)
    return {"service": svc, "status": "running" if check_service_running(svc) else "stopped"}


@app.get("/api/accounts")
async def list_accounts():
    await asyncio.to_thread(sync_live_traffic_and_ips)

    with get_db() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
        ip_rows = conn.execute(
            "SELECT username, protocol, client_ip, last_seen FROM active_ips WHERE last_seen >= ? ORDER BY id ASC",
            (int(time.time()) - 45,),
        ).fetchall()

    ip_map: Dict[str, List[str]] = {}
    for ir in ip_rows:
        key = f"{ir['protocol']}:{ir['username'].lower()}"
        lst = ip_map.setdefault(key, [])
        if ir["client_ip"] not in lst:
            lst.append(ir["client_ip"])

    result = []
    for r in rows:
        item = dict(r)
        item["up_bytes"] = int(item.get("up_bytes") or 0)
        item["down_bytes"] = int(item.get("down_bytes") or 0)
        ip_lim = max(1, int(item.get("ip_limit") or 2))
        key = f"{item['protocol']}:{item['username'].lower()}"
        connected_ips = ip_map.get(key, [])[:ip_lim] if item.get("status") == "active" else []
        item["connected_ips"] = connected_ips
        item["connected_ip_count"] = len(connected_ips)
        item["connection_details"] = build_account_connection_details(item)
        result.append(item)

    return {"accounts": result}


@app.post("/api/accounts")
async def create_account(request: Request, payload: CreateAccountRequest):
    is_admin = is_admin_request(request)
    def_ip_limit, def_quota_gb = get_public_default_limits()
    eff_ip_limit = max(1, int(payload.ip_limit)) if is_admin else def_ip_limit
    eff_quota_gb = max(0.0, float(payload.quota_gb)) if is_admin else def_quota_gb
    proto = payload.protocol.lower().strip()
    if proto not in ("ssh", "vmess", "vless", "trojan"):
        raise HTTPException(status_code=400, detail="Protokol harus ssh, vmess, vless, atau trojan.")

    username = payload.username.strip()
    if not username or not username.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Username hanya boleh berisi huruf, angka, '-', atau '_'.")

    if proto == "ssh":
        secret = (payload.secret or "").strip()
        if not secret:
            raise HTTPException(status_code=400, detail="Password wajib diisi untuk pembuatan akun SSH.")
    else:
        secret = str(uuid.uuid4())

    now_ts = int(time.time())
    is_trial = 0
    if payload.duration_mode == "trial_minutes":
        exp_ts = now_ts + (max(1, payload.duration_value) * 60)
        is_trial = 1
    elif payload.duration_mode == "trial_hours":
        exp_ts = now_ts + (max(1, payload.duration_value) * 3600)
        is_trial = 1
    else:
        exp_ts = now_ts + (max(1, payload.duration_value) * 86400)

    exp_dt = datetime.datetime.fromtimestamp(exp_ts)
    exp_display = exp_dt.strftime("%Y-%m-%d %H:%M")
    exp_date_linux = (exp_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    with get_db() as conn:
        exists = conn.execute("SELECT id FROM accounts WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail=f"Username '{username}' sudah terdaftar.")

        conn.execute(
            """
            INSERT INTO accounts (protocol, username, secret, exp_timestamp, exp_display, is_trial, ip_limit, quota_gb, up_bytes, down_bytes, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 'active')
            """,
            (proto, username, secret, exp_ts, exp_display, is_trial, eff_ip_limit, eff_quota_gb),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()

    if proto == "ssh":
        provision_linux_ssh_user(username, secret, exp_date_linux)
    else:
        sync_xray_accounts_to_config()

    acc_dict = dict(row)
    acc_dict["connected_ips"] = []
    acc_dict["connected_ip_count"] = 0
    acc_dict["connection_details"] = build_account_connection_details(acc_dict)
    return {"status": "created", "account": acc_dict}


@app.put("/api/accounts/{account_id}/renew")
async def renew_account(request: Request, account_id: int, payload: RenewAccountRequest):
    require_admin(request)
    OVERLIMIT_BLOCKED_IPS.clear()
    sync_blocked_ips_map_to_nginx()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")

        base_ts = max(int(time.time()), int(row["exp_timestamp"]))
        new_exp_ts = base_ts + (max(1, payload.add_days) * 86400)
        exp_dt = datetime.datetime.fromtimestamp(new_exp_ts)
        exp_display = exp_dt.strftime("%Y-%m-%d %H:%M")

        new_quota = payload.quota_gb if payload.quota_gb is not None else row["quota_gb"]
        new_ip_limit = payload.ip_limit if payload.ip_limit is not None else row["ip_limit"]

        conn.execute("DELETE FROM active_ips WHERE LOWER(username) = LOWER(?)", (row["username"],))
        conn.execute(
            """
            UPDATE accounts
            SET exp_timestamp = ?, exp_display = ?, quota_gb = ?, ip_limit = ?, status = 'active', is_trial = 0
            WHERE id = ?
            """,
            (new_exp_ts, exp_display, new_quota, new_ip_limit, account_id),
        )
        conn.commit()

    if row["protocol"] == "ssh":
        exp_date_linux = (exp_dt + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        provision_linux_ssh_user(row["username"], row["secret"], exp_date_linux)
        lock_unlock_linux_ssh_user(row["username"], lock=False)
    else:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {"status": "renewed", "exp_display": exp_display}


@app.post("/api/accounts/{account_id}/rules")
async def edit_account_rules(request: Request, account_id: int, payload: EditRulesRequest):
    require_admin(request)
    OVERLIMIT_BLOCKED_IPS.clear()
    sync_blocked_ips_map_to_nginx()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")

        new_ip_limit = max(1, int(payload.ip_limit))
        new_quota = max(0.0, float(payload.quota_gb))

        conn.execute("DELETE FROM active_ips WHERE LOWER(username) = LOWER(?)", (row["username"],))
        conn.execute(
            "UPDATE accounts SET ip_limit = ?, quota_gb = ?, status = 'active' WHERE id = ?",
            (new_ip_limit, new_quota, account_id),
        )
        conn.commit()

    if row["protocol"] == "ssh":
        lock_unlock_linux_ssh_user(row["username"], lock=False)
    else:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {
        "status": "rules_updated",
        "username": row["username"],
        "ip_limit": new_ip_limit,
        "quota_gb": new_quota,
    }


@app.post("/api/accounts/{account_id}/reset-usage")
async def reset_account_usage(request: Request, account_id: int):
    require_admin(request)
    OVERLIMIT_BLOCKED_IPS.clear()
    sync_blocked_ips_map_to_nginx()
    sync_live_traffic_and_ips(force=True)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")

        old_up = int(row["up_bytes"] or 0)
        old_dn = int(row["down_bytes"] or 0)
        deduct_traffic_from_daily(conn, old_up, old_dn)

        conn.execute("DELETE FROM active_ips WHERE LOWER(username) = LOWER(?)", (row["username"],))
        conn.execute(
            "UPDATE accounts SET up_bytes = 0, down_bytes = 0, status = 'active' WHERE id = ?",
            (account_id,),
        )
        reconcile_daily_traffic_with_accounts(conn)
        conn.commit()

    if row["protocol"] == "ssh":
        lock_unlock_linux_ssh_user(row["username"], lock=False)
    else:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {"status": "usage_reset", "username": row["username"]}


@app.post("/api/accounts/{account_id}/unlock")
async def unlock_account_and_reset_ip(request: Request, account_id: int, payload: UnlockAccountRequest):
    require_admin(request)
    OVERLIMIT_BLOCKED_IPS.clear()
    sync_blocked_ips_map_to_nginx()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")

        new_ip_limit = payload.ip_limit if (payload.ip_limit and payload.ip_limit > 0) else row["ip_limit"]
        conn.execute("DELETE FROM active_ips WHERE LOWER(username) = LOWER(?)", (row["username"],))
        conn.execute(
            "UPDATE accounts SET status = 'active', ip_limit = ? WHERE id = ?",
            (new_ip_limit, account_id),
        )
        conn.commit()

    if row["protocol"] == "ssh":
        lock_unlock_linux_ssh_user(row["username"], lock=False)
    else:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {"status": "unlocked", "username": row["username"], "ip_limit": new_ip_limit}


@app.delete("/api/accounts/{account_id}")
async def delete_account_admin_only(request: Request, account_id: int):
    require_admin(request)
    OVERLIMIT_BLOCKED_IPS.clear()
    sync_blocked_ips_map_to_nginx()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Akun tidak ditemukan.")
        uname = row["username"]
        proto = row["protocol"]
        up_b = int(row["up_bytes"] or 0)
        dn_b = int(row["down_bytes"] or 0)

        deduct_traffic_from_daily(conn, up_b, dn_b)
        conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
        conn.execute("DELETE FROM active_ips WHERE LOWER(username) = LOWER(?) AND protocol = ?", (uname, proto))
        reconcile_daily_traffic_with_accounts(conn)
        conn.commit()

    if proto == "ssh":
        subprocess.run(["pkill", "-u", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["userdel", "-f", uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        sync_xray_accounts_to_config(skip_traffic_sync=True)

    return {"status": "deleted", "username": uname, "protocol": proto}


@app.get("/api/monitor/status")
async def monitor_accounts_and_ips():
    return await list_accounts()


LOG_SOURCES = {
    "xray_access": os.path.join(LOG_DIR, "xray_access.log"),
    "xray_error": os.path.join(LOG_DIR, "xray_error.log"),
    "nginx_access": os.path.join(LOG_DIR, "nginx_access.log"),
    "ssh_ws": os.path.join(LOG_DIR, "ssh_ws.log"),
    "cloudflared": os.path.join(LOG_DIR, "cloudflared.log"),
    "monitor": os.path.join(LOG_DIR, "monitor.log"),
}


@app.get("/api/logs")
async def get_server_logs(request: Request, source: str = "xray_access", lines: int = 100):
    require_admin(request)
    target_path = LOG_SOURCES.get(source, LOG_SOURCES["xray_access"])
    content = ""
    if os.path.exists(target_path):
        try:
            n_lines = min(max(20, lines), 300)
            content = "".join(tail_file_lines(target_path, max_lines=n_lines, max_bytes=131072))
        except Exception as exc:
            content = f"Gagal membaca log: {exc}"
    else:
        content = f"File log ({target_path}) belum berisi data."

    return {"source": source, "path": target_path, "content": content}


@app.post("/api/logs/clear")
async def clear_server_log(request: Request, source: str = "xray_access"):
    require_admin(request)
    target_path = LOG_SOURCES.get(source)
    if target_path and os.path.exists(target_path):
        with open(target_path, "w", encoding="utf-8") as f:
            f.write("")
    return {"status": "cleared", "source": source}


DEFAULT_BANNER = (
    '<font color="#00f2fe"><b>====================================================</b></font><br>\n'
    '<font color="#a855f7"><b>        🚀 JIBSZ TUNNELING / J1BTNL SERVER 🚀       </b></font><br>\n'
    '<font color="#00f2fe"><b>====================================================</b></font><br>\n'
    '<font color="#10b981"><b> • No DDoS / No Flooding / No Torrent / No Spam     </b></font><br>\n'
    '<font color="#f59e0b"><b> • Support & Credit Telegram : @admj1btnl           </b></font><br>\n'
    '<font color="#00f2fe"><b>====================================================</b></font>'
)


@app.get("/api/settings/banner")
async def get_ssh_banner(request: Request):
    require_admin(request)
    if os.path.exists(BANNER_FILE):
        try:
            with open(BANNER_FILE, "r", encoding="utf-8", errors="ignore") as f:
                return {"banner_html": f.read()}
        except Exception:
            pass
    return {"banner_html": DEFAULT_BANNER}


@app.post("/api/settings/banner")
async def save_ssh_banner(request: Request, payload: BannerUpdateRequest):
    require_admin(request)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(BANNER_FILE, "w", encoding="utf-8") as f:
        f.write(payload.banner_html)
    subprocess.run(["systemctl", "restart", "dropbear"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["systemctl", "reload", "ssh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "saved"}


@app.get("/api/settings/backups")
async def list_backups(request: Request):
    require_admin(request)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    files = []
    for fname in sorted(os.listdir(BACKUP_DIR), reverse=True):
        if fname.endswith(".tar.gz"):
            fpath = os.path.join(BACKUP_DIR, fname)
            files.append({
                "filename": fname,
                "size_kb": round(os.path.getsize(fpath) / 1024, 1),
                "created_at": datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return {"backups": files}


@app.post("/api/settings/backup")
async def create_backup_archive(request: Request):
    require_admin(request)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    try:
        with get_db() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception:
        pass
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    fname = f"jibsz-backup-{ts}.tar.gz"
    fpath = os.path.join(BACKUP_DIR, fname)

    with tarfile.open(fpath, "w:gz") as tar:
        if os.path.exists(CONFIG_DIR):
            tar.add(CONFIG_DIR, arcname="etc/jibsz")
        if os.path.exists(XRAY_CONFIG_PATH):
            tar.add(XRAY_CONFIG_PATH, arcname="usr/local/etc/xray/config.json")

    return {"status": "created", "filename": fname}


@app.get("/api/settings/backup/download/{filename}")
async def download_backup_archive(request: Request, filename: str):
    require_admin(request)
    safe_name = os.path.basename(filename)
    fpath = os.path.join(BACKUP_DIR, safe_name)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="File backup tidak ditemukan.")
    return FileResponse(fpath, media_type="application/gzip", filename=safe_name)


def _apply_post_restore_sync() -> int:
    ensure_admin_auth_defaults()
    sync_xray_accounts_to_config(skip_traffic_sync=True)
    ssh_synced = sync_ssh_accounts_to_linux()
    if os.path.exists(BANNER_FILE):
        subprocess.run(["systemctl", "restart", "dropbear"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "reload", "ssh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return ssh_synced


@app.post("/api/settings/restore-local")
async def restore_from_local_backup(request: Request, payload: RestoreLocalRequest):
    require_admin(request)
    safe_name = os.path.basename(payload.filename)
    fpath = os.path.join(BACKUP_DIR, safe_name)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="File backup tidak ditemukan.")

    for wal_ext in ("-wal", "-shm"):
        try:
            os.remove(DB_PATH + wal_ext)
        except OSError:
            pass

    with tarfile.open(fpath, "r:gz") as tar:
        tar.extractall(path="/")

    ssh_synced = _apply_post_restore_sync()
    return {"status": "restored", "filename": safe_name, "ssh_synced": ssh_synced}


@app.post("/api/settings/restore-upload")
async def restore_from_uploaded_backup(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    safe_name = f"uploaded-{int(time.time())}-{os.path.basename(file.filename or 'backup.tar.gz')}"
    fpath = os.path.join(BACKUP_DIR, safe_name)

    content = await file.read()
    with open(fpath, "wb") as f:
        f.write(content)

    try:
        for wal_ext in ("-wal", "-shm"):
            try:
                os.remove(DB_PATH + wal_ext)
            except OSError:
                pass
        with tarfile.open(fpath, "r:gz") as tar:
            tar.extractall(path="/")
        ssh_synced = _apply_post_restore_sync()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Format arsip backup tidak valid: {exc}")

    return {"status": "restored", "filename": safe_name, "ssh_synced": ssh_synced}
