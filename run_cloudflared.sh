#!/bin/bash
set -e

if [ -z "${CF_TUNNEL_TOKEN:-}" ]; then
    echo "[JIBSZ-CF] CF_TUNNEL_TOKEN belum diisi di Railway Variables. Menunggu..."
    exec sleep infinity
fi

echo "[JIBSZ-CF] Menjalankan Cloudflare Tunnel (cloudflared)..."
exec /usr/local/bin/cloudflared tunnel --no-autoupdate run --token "${CF_TUNNEL_TOKEN}"
