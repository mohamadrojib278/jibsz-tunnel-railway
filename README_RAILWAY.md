# 🚀 Jibsz Tunneling / J1BTNL — Railway Deploy Edition (V4.9)

Versi ini dirancang khusus agar **Web Panel + VLESS WS + VMess WS + Trojan WS + SSH WebSocket** berjalan sekaligus di **satu port Railway (`$PORT`)** menggunakan Docker + Nginx Reverse Proxy tanpa memerlukan `systemd`.

## ✨ Fitur yang Tersedia
- **Web Panel Publik Read-Only + Login Admin** (`admin` / `jibsz123` atau via Environment Variable Railway).
- **Menu Publik:** Dashboard, Buat Akun, Sistem (tanpa akses ubah limit device, limit kuota, banner SSH, restart service, atau hapus akun).
- **Menu Admin:** Semua menu + Edit Limit Device & Kuota, Ubah Banner SSH, Hapus Akun, Log Server, Backup/Restore.
- **Hostname Dinamis** di pojok kanan atas mengikuti domain Railway / custom domain yang sedang dibuka.
- **Routing 1 Port Railway (`$PORT`):**
  - `/` → Web Panel
  - `/vless` → VLESS WebSocket (TLS 443 via Railway Edge)
  - `/vmess` → VMess WebSocket (TLS 443 via Railway Edge)
  - `/trojan` → Trojan WebSocket (TLS 443 via Railway Edge)
  - `/ssh-ws` → SSH WebSocket (Dropbear + SSH-WS Proxy)

## 🛠️ Cara Deploy ke Railway
1. **Upload folder `jibsz-tunnel-railway`** ke repositori GitHub kamu (atau deploy langsung via `railway up`).
2. Di dashboard **Railway**:
   - Pilih **New Project → Deploy from GitHub Repo** dan pilih repositori tersebut.
   - Railway otomatis mendeteksi `Dockerfile` dan `railway.json`.
3. **Aktifkan Public Domain:**
   - Masuk ke tab **Settings → Networking → Generate Domain** (atau tambahkan Custom Domain).
4. **Pasang Volume (Disarankan agar akun tidak hilang saat redeploy):**
   - Masuk ke tab **Volumes → Add Volume**, isi Mount Path: **`/data`**.
5. **Variables (Opsional):**
   - `ADMIN_USER` — Username admin (default: `admin`)
   - `ADMIN_PASS` — Password admin (default: `jibsz123`)
   - `PANEL_DOMAIN` — Custom domain (opsional; otomatis pakai `RAILWAY_PUBLIC_DOMAIN`)
   - Build Arg `XRAY_VERSION` — default `v26.11.9` (bisa diubah ke `v1.8.24` jika ingin versi LTS)

## 📌 Catatan Railway
- Railway secara bawaan mem-proxy trafik **HTTP/HTTPS & WebSocket (port 443/80)** ke `$PORT`, jadi koneksi **WS TLS 443** (VLESS, VMess, Trojan, SSH-WS) langsung aktif.
- Protokol gRPC murni atau SSH TCP port 22 langsung membutuhkan VPS (bukan HTTP edge proxy PaaS).
