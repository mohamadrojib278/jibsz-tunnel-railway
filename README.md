# Jibsz Tunneling V4.9.1 — Railway + Cloudflare Tunnel Edition

Paket ini sudah terintegrasi penuh dengan **Cloudflare Tunnel (`cloudflared`)** sebagai koneksi utama di Railway. Anda tidak perlu membuka domain publik Railway.

## Pengaturan Wajib di Railway (Variables)
Tambahkan 2 variabel berikut pada menu **Variables** di service Railway Anda:
- `CF_TUNNEL_TOKEN` = Token tunnel dari Cloudflare Zero Trust
- `DOMAIN` = Domain utama Anda (contoh: `j1btnl.eu.cc` atau `tunnel.domainanda.com`)
- *(Opsional)* `PORT` = `8080` (sudah default 8080)

## Pengaturan Public Hostname di Cloudflare Zero Trust
Pada menu **Networks > Tunnels > [Tunnel Anda] > Public Hostname > Add a public hostname**:
- **Subdomain / Domain**: sesuai `DOMAIN` Anda
- **Service Type**: `HTTP`
- **URL**: `127.0.0.1:8080`
