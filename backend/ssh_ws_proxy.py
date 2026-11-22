#!/usr/bin/env python3
"""
JIBSZ TUNNELING / J1BTNL - SSH WEBSOCKET PROXY & AUTO-BLOCK ENFORCER V4.6 (ssh_ws_proxy.py)
Author  : Jibsz Tunneling (@admj1btnl)
Support : - SSH WebSocket pada / dan /ssh-ws -> Dropbear (127.0.0.1:109) / OpenSSH (127.0.0.1:22)
          - Auto-Block Akun (status = 'blocked', usermod -L, pkill -u) saat jumlah IP aktif
            melebihi batas Devices Limit (ip_limit) atau kuota (quota_gb)
"""

import asyncio
import base64
import datetime
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from typing import Dict, Any, Tuple, Optional, List

LISTEN_HOST = os.environ.get("SSH_WS_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("SSH_WS_PORT", "10015"))
DEFAULT_TARGET_HOST = "127.0.0.1"
DROPBEAR_PORT = int(os.environ.get("DROPBEAR_PORT", "109"))
OPENSSH_PORT = int(os.environ.get("OPENSSH_PORT", "22"))
WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
DB_PATH = os.environ.get("DB_PATH", "/etc/jibsz/jibsz.db")
BLOCKED_IPS_MAP_FILE = "/etc/jibsz/blocked_ips.map"

XRAY_PORT_MAP = {
    "/vless": 10001,
    "/vmess": 10002,
    "/trojan": 10003,
}

PORT_TO_PROTO = {
    10001: "vless",
    10002: "vmess",
    10003: "trojan",
    10004: "vless",
    10005: "vmess",
    10006: "trojan",
    10015: "ssh",
}

LOG_DIR = os.environ.get("LOG_DIR", "/var/log/jibsz")
try:
    os.makedirs(LOG_DIR, exist_ok=True)
except Exception:
    LOG_DIR = "/tmp"
LOG_FILE = os.path.join(LOG_DIR, "ssh_ws.log")
ACTIVE_CONN_FILE = os.path.join(LOG_DIR, "ssh_ws_active.json")
XRAY_PORT_IP_MAP_FILE = os.path.join(LOG_DIR, "xray_port_ip_map.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

LAST_JOURNALCTL_TS = 0.0
STREAM_IDLE_TIMEOUT = 1800.0


def tail_file_lines(filepath: str, max_lines: int = 180, max_bytes: int = 65536) -> List[str]:
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
        lines = text.splitlines()
        if size > read_size and len(lines) > 1:
            lines = lines[1:]
        return lines[-max_lines:]
    except Exception:
        return []

ACTIVE_CONNECTIONS: Dict[str, Dict[str, Any]] = {}
LOOPBACK_PORT_MAP: Dict[str, Dict[str, Any]] = {}
LOOPBACK_PROTO_LATEST: Dict[str, Dict[str, Any]] = {}
LAST_CF_IP_ON_PORT80: Tuple[str, float] = ("", 0.0)

HTTP_METHODS = (
    b"GET ",
    b"GET-WSS ",
    b"POST ",
    b"CONNECT ",
    b"HEAD ",
    b"PUT ",
    b"OPTIONS ",
    b"PATCH ",
)


def is_valid_public_ip(ip_str: str) -> bool:
    if not ip_str:
        return False
    ip_clean = ip_str.strip().strip("[]")
    if ip_clean in ("127.0.0.1", "::1", "0.0.0.0", "-", "localhost"):
        return False
    if ip_clean.startswith("127.") or ip_clean.startswith("10.") or ip_clean.startswith("192.168."):
        return False
    return True


def is_ip_in_blocked_map(ip_str: str) -> bool:
    if not ip_str or not os.path.exists(BLOCKED_IPS_MAP_FILE):
        return False
    try:
        with open(BLOCKED_IPS_MAP_FILE, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if parts and parts[0] == ip_str:
                    return True
    except Exception:
        pass
    return False


def extract_real_ip_from_http_text(http_text: str) -> str:
    headers: Dict[str, str] = {}
    for line in http_text.split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    for h_name in ("cf-connecting-ip", "true-client-ip", "x-real-ip", "x-client-ip", "x-forwarded-for"):
        val = headers.get(h_name, "")
        if val:
            first_ip = val.split(",")[0].strip()
            if is_valid_public_ip(first_ip):
                return first_ip
    return ""


def save_loopback_ip_map() -> None:
    now_ts = int(time.time())
    expired_keys = [k for k, v in list(LOOPBACK_PORT_MAP.items()) if now_ts - int(v.get("ts", 0)) > 600]
    for k in expired_keys:
        LOOPBACK_PORT_MAP.pop(k, None)
    if len(LOOPBACK_PORT_MAP) > 1500:
        oldest = sorted(LOOPBACK_PORT_MAP.items(), key=lambda kv: int(kv[1].get("ts", 0)))[:-1000]
        for k, _ in oldest:
            LOOPBACK_PORT_MAP.pop(k, None)

    try:
        tmp_f = f"{XRAY_PORT_IP_MAP_FILE}.tmp"
        with open(tmp_f, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "port_map": LOOPBACK_PORT_MAP,
                    "proto_latest": LOOPBACK_PROTO_LATEST,
                    "updated_at": now_ts,
                },
                f,
            )
        os.replace(tmp_f, XRAY_PORT_IP_MAP_FILE)
    except Exception:
        pass


def passive_loopback_header_sniffer() -> None:
    global LAST_CF_IP_ON_PORT80
    try:
        raw_sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.ntohs(0x0003))
        raw_sock.bind(("lo", 0))
    except Exception as exc:
        logging.debug("Passive loopback sniffer tidak aktif: %s", exc)
        return

    last_save = 0.0
    while True:
        try:
            packet, _ = raw_sock.recvfrom(65535)
            if len(packet) < 54:
                continue
            eth_proto = struct.unpack("!H", packet[12:14])[0]
            if eth_proto != 0x0800:
                continue
            ip_header = packet[14:34]
            iph = struct.unpack("!BBHHHBBH4s4s", ip_header)
            protocol = iph[6]
            if protocol != 6:
                continue
            ihl = (iph[0] & 0xF) * 4
            tcp_start = 14 + ihl
            if len(packet) < tcp_start + 20:
                continue
            tcph = struct.unpack("!HHLLBBHHH", packet[tcp_start:tcp_start + 20])
            src_port = tcph[0]
            dst_port = tcph[1]
            doff = (tcph[4] >> 4) * 4
            payload_start = tcp_start + doff
            if len(packet) <= payload_start:
                continue

            if dst_port not in (80, 443, 10001, 10002, 10003, 10004, 10005, 10006, 10015):
                continue

            payload = packet[payload_start:]
            if b"HTTP/1." not in payload and b":path" not in payload and b"cf-connecting-ip" not in payload.lower():
                continue

            text = payload.decode("utf-8", errors="ignore")
            real_ip = extract_real_ip_from_http_text(text)
            now_f = time.time()

            if dst_port in (80, 443) and real_ip:
                LAST_CF_IP_ON_PORT80 = (real_ip, now_f)
                low_text = text.lower()
                for p_key in ("vless", "vmess", "trojan", "ssh"):
                    if f"/{p_key}" in low_text:
                        LOOPBACK_PROTO_LATEST[p_key] = {"ip": real_ip, "ts": int(now_f)}
                        break

            if dst_port in PORT_TO_PROTO:
                proto_name = PORT_TO_PROTO[dst_port]
                if not real_ip and (now_f - LAST_CF_IP_ON_PORT80[1]) <= 3.0:
                    real_ip = LAST_CF_IP_ON_PORT80[0]
                if real_ip:
                    LOOPBACK_PORT_MAP[str(src_port)] = {
                        "ip": real_ip,
                        "proto": proto_name,
                        "dst_port": dst_port,
                        "ts": int(now_f),
                    }
                    LOOPBACK_PROTO_LATEST[proto_name] = {"ip": real_ip, "ts": int(now_f)}
                    if (now_f - last_save) >= 0.3:
                        save_loopback_ip_map()
                        last_save = now_f
        except Exception:
            time.sleep(0.2)


def resolve_ssh_usernames_from_auth_log() -> None:
    global LAST_JOURNALCTL_TS
    unresolved = [c for c in list(ACTIVE_CONNECTIONS.values()) if not c.get("username") and c.get("backend_local_port")]
    if not unresolved:
        return

    lines = []
    for af in ("/var/log/auth.log", "/var/log/secure", "/var/log/syslog"):
        if os.path.exists(af):
            lines.extend(tail_file_lines(af, max_lines=180, max_bytes=65536))

    now_f = time.time()
    if (now_f - LAST_JOURNALCTL_TS) >= 10.0:
        LAST_JOURNALCTL_TS = now_f
        try:
            j_proc = subprocess.run(
                ["journalctl", "--no-pager", "-n", "120"],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            if j_proc.returncode == 0 and j_proc.stdout:
                lines.extend(j_proc.stdout.splitlines())
        except Exception:
            pass

    if not lines:
        return

    port_to_user: Dict[int, str] = {}
    for line in lines:
        m_db = re.search(r"auth succeeded for '([^']+)' from [0-9a-fA-F\.:]+:(\d+)", line)
        if m_db:
            port_to_user[int(m_db.group(2))] = m_db.group(1)
            continue
        m_ssh = re.search(r"Accepted (?:password|publickey) for ([^\s]+) from [0-9a-fA-F\.:]+ port (\d+)", line)
        if m_ssh:
            port_to_user[int(m_ssh.group(2))] = m_ssh.group(1)

    for info in unresolved:
        lport = int(info.get("backend_local_port") or 0)
        if lport in port_to_user:
            info["username"] = port_to_user[lport]


def flush_ssh_traffic_and_ips_to_db() -> None:
    if not os.path.exists(DB_PATH):
        return

    resolve_ssh_usernames_from_auth_log()

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10.0)
        conn.row_factory = sqlite3.Row
        now_ts = int(time.time())
        today_str = datetime.date.today().strftime("%Y-%m-%d")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS traffic_daily (
                date_str TEXT PRIMARY KEY,
                up_bytes INTEGER DEFAULT 0,
                down_bytes INTEGER DEFAULT 0
            )
            """
        )

        ssh_rows = conn.execute(
            "SELECT id, username, ip_limit, quota_gb, up_bytes, down_bytes, status FROM accounts WHERE protocol = 'ssh' ORDER BY id ASC"
        ).fetchall()
        ssh_user_map = {r["username"].lower(): r for r in ssh_rows if r["status"] == "active"}
        active_ssh_rows = [r for r in ssh_rows if r["status"] == "active"]

        total_up_flushed = 0
        total_dn_flushed = 0
        ips_per_user: Dict[str, List[str]] = {}
        conns_per_user: Dict[str, List[Dict[str, Any]]] = {}
        active_ssh_entries = []

        sorted_conns = sorted(
            list(ACTIVE_CONNECTIONS.values()),
            key=lambda x: int(x.get("connected_at", 0)),
        )

        for info in sorted_conns:
            u_delta = int(info.get("pending_up", 0))
            d_delta = int(info.get("pending_dn", 0))

            cip = info.get("client_ip", "")
            uname = (info.get("username") or "").strip()

            target_row = ssh_user_map.get(uname.lower()) if uname else None
            if target_row is None and len(active_ssh_rows) == 1:
                target_row = active_ssh_rows[0]
                info["username"] = target_row["username"]

            if target_row is not None:
                real_uname = target_row["username"]
                conns_per_user.setdefault(real_uname, []).append(info)
                if is_valid_public_ip(cip):
                    u_ips = ips_per_user.setdefault(real_uname, [])
                    if cip not in u_ips:
                        u_ips.append(cip)

                info["pending_up"] = 0
                info["pending_dn"] = 0
                if u_delta > 0 or d_delta > 0:
                    conn.execute(
                        "UPDATE accounts SET up_bytes = IFNULL(up_bytes, 0) + ?, down_bytes = IFNULL(down_bytes, 0) + ? WHERE id = ?",
                        (u_delta, d_delta, target_row["id"]),
                    )
                    total_up_flushed += u_delta
                    total_dn_flushed += d_delta

        # Cek pelanggaran Devices Limit (ip_limit) atau Bandwidth Limit (quota_gb) untuk akun SSH
        for r in active_ssh_rows:
            real_uname = r["username"]
            ip_lim = max(1, int(r["ip_limit"] or 2))
            u_ips = ips_per_user.get(real_uname, [])

            cur_row = conn.execute("SELECT up_bytes, down_bytes, quota_gb FROM accounts WHERE id = ?", (r["id"],)).fetchone()
            tot_used = int(cur_row["up_bytes"] or 0) + int(cur_row["down_bytes"] or 0) if cur_row else 0
            q_bytes = int(float(cur_row["quota_gb"] or 0) * (1024 ** 3)) if cur_row else 0

            if len(u_ips) > ip_lim or (q_bytes > 0 and tot_used >= q_bytes):
                # Ubah status akun menjadi 'blocked' & putus semua sesi
                logging.warning("SSH Account '%s' BLOCKED (IP aktif: %d/%d, Used: %d B)", real_uname, len(u_ips), ip_lim, tot_used)
                conn.execute("UPDATE accounts SET status = 'blocked' WHERE id = ?", (r["id"],))
                subprocess.run(["usermod", "-L", real_uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pkill", "-u", real_uname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for c_info in conns_per_user.get(real_uname, []):
                    for w_obj in (c_info.get("_client_writer"), c_info.get("_backend_writer")):
                        if w_obj is not None:
                            try:
                                w_obj.close()
                            except Exception:
                                pass
                    ACTIVE_CONNECTIONS.pop(c_info.get("conn_id", ""), None)
            else:
                for cip in u_ips:
                    active_ssh_entries.append((real_uname, cip, now_ts))

        if total_up_flushed > 0 or total_dn_flushed > 0:
            conn.execute(
                """
                INSERT INTO traffic_daily (date_str, up_bytes, down_bytes)
                VALUES (?, ?, ?)
                ON CONFLICT(date_str) DO UPDATE SET
                    up_bytes = IFNULL(up_bytes, 0) + excluded.up_bytes,
                    down_bytes = IFNULL(down_bytes, 0) + excluded.down_bytes
                """,
                (today_str, total_up_flushed, total_dn_flushed),
            )

        conn.execute("DELETE FROM active_ips WHERE protocol = 'ssh'")
        for uname_val, cip_val, seen_ts in active_ssh_entries:
            conn.execute(
                "INSERT OR REPLACE INTO active_ips (username, protocol, client_ip, last_seen) VALUES (?, 'ssh', ?, ?)",
                (uname_val, cip_val, seen_ts),
            )

        conn.commit()
        conn.close()
    except Exception as exc:
        logging.debug("DB flush error: %s", exc)


def persist_active_connections() -> None:
    flush_ssh_traffic_and_ips_to_db()
    try:
        tmp_path = f"{ACTIVE_CONN_FILE}.tmp"
        clean_list = []
        for v in ACTIVE_CONNECTIONS.values():
            clean_list.append({
                "conn_id": v.get("conn_id"),
                "username": v.get("username", ""),
                "client_ip": v.get("client_ip", ""),
                "backend_port": v.get("backend_port", 0),
                "backend_local_port": v.get("backend_local_port", 0),
                "connected_at": v.get("connected_at", 0),
                "last_seen": v.get("last_seen", 0),
                "up_bytes": v.get("up_bytes", 0),
                "down_bytes": v.get("down_bytes", 0),
            })
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(clean_list, f, indent=2)
        os.replace(tmp_path, ACTIVE_CONN_FILE)
    except Exception as exc:
        logging.debug("Gagal menyimpan active connections: %s", exc)


def detect_backend_port() -> int:
    for port in (DROPBEAR_PORT, OPENSSH_PORT):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex((DEFAULT_TARGET_HOST, port)) == 0:
                return port
    return OPENSSH_PORT


def parse_http_headers(headers_raw: str, peer_ip: str) -> Tuple[str, str]:
    headers: Dict[str, str] = {}
    for line in headers_raw.split("\r\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()

    real_ip = extract_real_ip_from_http_text(headers_raw)
    if not real_ip:
        if is_valid_public_ip(peer_ip):
            real_ip = peer_ip
        elif (time.time() - LAST_CF_IP_ON_PORT80[1]) <= 5.0 and LAST_CF_IP_ON_PORT80[0]:
            real_ip = LAST_CF_IP_ON_PORT80[0]
        else:
            real_ip = peer_ip

    ws_key = headers.get("sec-websocket-key", "")
    return real_ip, ws_key


def detect_xray_ws_route(initial_data: bytes) -> Optional[Tuple[int, str, bytes]]:
    text = initial_data.decode("utf-8", errors="ignore")
    matches = list(re.finditer(r"(?:GET|GET-WSS|POST)\s+/(vless|vmess|trojan)([^\s]*)\s+HTTP/1\.[01]", text, re.IGNORECASE))
    if not matches:
        return None

    last_match = matches[-1]
    proto_name = last_match.group(1).lower()
    canonical_path = f"/{proto_name}"
    xray_port = XRAY_PORT_MAP[canonical_path]

    start_idx = last_match.start()
    sub_text = text[start_idx:]
    header_end = sub_text.find("\r\n\r\n")
    if header_end == -1:
        header_block = sub_text
        body_rem = b""
    else:
        header_block = sub_text[:header_end]
        raw_start = initial_data.find(header_block.encode("utf-8", errors="ignore"))
        if raw_start != -1:
            raw_end = initial_data.find(b"\r\n\r\n", raw_start)
            body_rem = initial_data[raw_end + 4:] if raw_end != -1 else b""
        else:
            body_rem = b""

    lines = header_block.split("\r\n")
    suffix = last_match.group(2) or ""
    query_part = ""
    if "?" in suffix:
        query_part = "?" + suffix.split("?", 1)[1]
    lines[0] = f"GET {canonical_path}{query_part} HTTP/1.1"

    has_upgrade = any(l.lower().startswith("upgrade:") for l in lines[1:])
    has_conn = any(l.lower().startswith("connection:") for l in lines[1:])
    has_ws_key = any(l.lower().startswith("sec-websocket-key:") for l in lines[1:])
    has_ws_ver = any(l.lower().startswith("sec-websocket-version:") for l in lines[1:])

    out_lines = [lines[0]]
    for line in lines[1:]:
        low = line.lower()
        if low.startswith("upgrade:"):
            out_lines.append("Upgrade: websocket")
        elif low.startswith("connection:"):
            out_lines.append("Connection: Upgrade")
        else:
            out_lines.append(line)

    if not has_upgrade:
        out_lines.append("Upgrade: websocket")
    if not has_conn:
        out_lines.append("Connection: Upgrade")
    if not has_ws_key:
        out_lines.append("Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==")
    if not has_ws_ver:
        out_lines.append("Sec-WebSocket-Version: 13")

    rebuilt = ("\r\n".join(out_lines) + "\r\n\r\n").encode("utf-8") + body_rem
    return xray_port, canonical_path, rebuilt


def build_101_response(ws_key: str = "") -> bytes:
    lines = [
        "HTTP/1.1 101 Switching Protocols",
        "Upgrade: websocket",
        "Connection: Upgrade",
    ]
    if ws_key:
        accept_val = base64.b64encode(
            hashlib.sha1((ws_key + WS_MAGIC_GUID).encode("utf-8")).digest()
        ).decode("ascii")
        lines.append(f"Sec-WebSocket-Accept: {accept_val}")
    lines.append("Server: Jibsz-Tunnel-WS/4.6")
    lines.append("")
    lines.append("")
    return "\r\n".join(lines).encode("utf-8")


async def pipe_raw_stream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while not reader.at_eof():
            chunk = await asyncio.wait_for(reader.read(32768), timeout=STREAM_IDLE_TIMEOUT)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def pipe_client_to_backend(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    conn_id: str,
    ssh_started: bool,
) -> None:
    has_seen_ssh_banner = ssh_started
    try:
        while not reader.at_eof():
            chunk = await asyncio.wait_for(reader.read(16384), timeout=STREAM_IDLE_TIMEOUT)
            if not chunk:
                break

            if not has_seen_ssh_banner:
                if b"SSH-2.0-" in chunk:
                    idx = chunk.find(b"SSH-2.0-")
                    chunk = chunk[idx:]
                    has_seen_ssh_banner = True
                elif chunk.lstrip().startswith(HTTP_METHODS):
                    continue
                else:
                    has_seen_ssh_banner = True

            writer.write(chunk)
            await writer.drain()
            n_bytes = len(chunk)
            if conn_id in ACTIVE_CONNECTIONS:
                ACTIVE_CONNECTIONS[conn_id]["up_bytes"] += n_bytes
                ACTIVE_CONNECTIONS[conn_id]["pending_up"] = ACTIVE_CONNECTIONS[conn_id].get("pending_up", 0) + n_bytes
                ACTIVE_CONNECTIONS[conn_id]["last_seen"] = int(time.time())
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def pipe_backend_to_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    conn_id: str,
) -> None:
    try:
        while not reader.at_eof():
            chunk = await asyncio.wait_for(reader.read(16384), timeout=STREAM_IDLE_TIMEOUT)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
            n_bytes = len(chunk)
            if conn_id in ACTIVE_CONNECTIONS:
                ACTIVE_CONNECTIONS[conn_id]["down_bytes"] += n_bytes
                ACTIVE_CONNECTIONS[conn_id]["pending_dn"] = ACTIVE_CONNECTIONS[conn_id].get("pending_dn", 0) + n_bytes
                ACTIVE_CONNECTIONS[conn_id]["last_seen"] = int(time.time())
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def handle_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    peername = client_writer.get_extra_info("peername")
    peer_ip = peername[0] if peername else "127.0.0.1"
    peer_port = peername[1] if peername else 0
    conn_id = f"{peer_ip}:{peer_port}:{int(time.time() * 1000)}"

    try:
        initial_data = await asyncio.wait_for(client_reader.read(8192), timeout=12.0)
    except Exception:
        client_writer.close()
        return

    if not initial_data:
        client_writer.close()
        return

    xray_route = detect_xray_ws_route(initial_data)
    if xray_route is not None:
        xray_port, canonical_path, forwarded_bytes = xray_route
        try:
            xray_reader, xray_writer = await asyncio.wait_for(
                asyncio.open_connection(DEFAULT_TARGET_HOST, xray_port),
                timeout=5.0,
            )
            xray_writer.write(forwarded_bytes)
            await xray_writer.drain()
            await asyncio.gather(
                pipe_raw_stream(client_reader, xray_writer),
                pipe_raw_stream(xray_reader, client_writer),
            )
        except Exception as exc:
            logging.error("Gagal meneruskan %s ke Xray 127.0.0.1:%d - %s", canonical_path, xray_port, exc)
            client_writer.close()
        return

    text_preview = initial_data.decode("utf-8", errors="ignore")
    real_ip = peer_ip
    leftover_payload = b""
    ssh_banner_already_seen = False

    if initial_data.lstrip().startswith(HTTP_METHODS):
        real_ip, ws_key = parse_http_headers(text_preview, peer_ip)
        if is_ip_in_blocked_map(real_ip):
            client_writer.write(b"HTTP/1.1 403 Forbidden (Account Blocked - Device Limit Exceeded)\r\nConnection: close\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
            return

        handshake_resp = build_101_response(ws_key)
        client_writer.write(handshake_resp)
        await client_writer.drain()

        if b"SSH-2.0-" in initial_data:
            idx = initial_data.find(b"SSH-2.0-")
            leftover_payload = initial_data[idx:]
            ssh_banner_already_seen = True
    else:
        if is_ip_in_blocked_map(real_ip):
            client_writer.close()
            return
        leftover_payload = initial_data
        if b"SSH-2.0-" in initial_data:
            ssh_banner_already_seen = True

    target_port = detect_backend_port()
    try:
        backend_reader, backend_writer = await asyncio.wait_for(
            asyncio.open_connection(DEFAULT_TARGET_HOST, target_port),
            timeout=5.0,
        )
    except Exception as exc:
        logging.error("Gagal menghubungi backend SSH/Dropbear di %s:%d - %s", DEFAULT_TARGET_HOST, target_port, exc)
        client_writer.close()
        return

    sock_info = backend_writer.get_extra_info("sockname")
    backend_local_port = sock_info[1] if sock_info and len(sock_info) >= 2 else 0

    ACTIVE_CONNECTIONS[conn_id] = {
        "conn_id": conn_id,
        "username": "",
        "client_ip": real_ip,
        "backend_port": target_port,
        "backend_local_port": backend_local_port,
        "connected_at": int(time.time()),
        "last_seen": int(time.time()),
        "up_bytes": 0,
        "down_bytes": 0,
        "pending_up": 0,
        "pending_dn": 0,
        "_client_writer": client_writer,
        "_backend_writer": backend_writer,
    }
    logging.info("SSH-WS Connected | Client IP: %s -> Backend: 127.0.0.1:%d (local:%d)", real_ip, target_port, backend_local_port)

    if leftover_payload:
        backend_writer.write(leftover_payload)
        await backend_writer.drain()

    try:
        await asyncio.gather(
            pipe_client_to_backend(client_reader, backend_writer, conn_id, ssh_banner_already_seen),
            pipe_backend_to_client(backend_reader, client_writer, conn_id),
        )
    finally:
        info = ACTIVE_CONNECTIONS.pop(conn_id, None)
        if info and (info.get("pending_up", 0) > 0 or info.get("pending_dn", 0) > 0):
            ACTIVE_CONNECTIONS[conn_id] = info
            try:
                await asyncio.to_thread(flush_ssh_traffic_and_ips_to_db)
            except Exception:
                pass
            ACTIVE_CONNECTIONS.pop(conn_id, None)
        for w_obj in (client_writer, backend_writer):
            try:
                if not w_obj.is_closing():
                    w_obj.close()
            except Exception:
                pass
        logging.info("SSH-WS Disconnected | Client IP: %s", real_ip)


async def periodic_sync() -> None:
    while True:
        await asyncio.sleep(4)
        try:
            await asyncio.to_thread(persist_active_connections)
            await asyncio.to_thread(save_loopback_ip_map)
        except Exception:
            pass


async def main() -> None:
    sniffer_thread = threading.Thread(target=passive_loopback_header_sniffer, daemon=True)
    sniffer_thread.start()

    persist_active_connections()
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    addrs = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    logging.info("Jibsz SSH WebSocket Proxy V4.6 aktif di %s (Dropbear:%d / OpenSSH:%d)", addrs, DROPBEAR_PORT, OPENSSH_PORT)

    sync_task = asyncio.create_task(periodic_sync())
    stop_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    async with server:
        await stop_event.wait()
        sync_task.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
