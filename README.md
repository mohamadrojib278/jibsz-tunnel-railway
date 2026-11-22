# 🚀 Jibsz Tunneling AutoInstall & Web Panel V2 (J1BTNL Edition)

![Platform](https://img.shields.io/badge/OS-Ubuntu_22.04_%7C_Debian_12-00f2fe?style=for-the-badge&logo=linux)
![Mode](https://img.shields.io/badge/Network-VPS_Public_%7C_VPS_NAT_Cloudflare_Tunnel-7b2ff7?style=for-the-badge&logo=cloudflare)
![UI](https://img.shields.io/badge/UI_V2-Top_Left_Menu_%7C_Public_No_Login_%7C_CLI_Delete-10b981?style=for-the-badge)
![Author](https://img.shields.io/badge/Telegram-%40admj1btnl-26A5E4?style=for-the-badge&logo=telegram)

**Jibsz Tunneling / J1BTNL V2** adalah paket **Autoscript & Web Installer Tunneling Multi-Protokol** dengan antarmuka **Web Panel V2 Ringkas & Mobile-First** (menggunakan tombol menu *slide drawer* di kiri atas sehingga tidak memenuhi layar HP Android maupun Desktop) serta **Menu Terminal CLI (`menu` / `jibsz`)** yang sinkron secara real-time menggunakan database SQLite terpusat (`/etc/jibsz/jibsz.db`).

---

## ✨ Pembaruan Tampilan V2 (Mobile-Friendly & Simpel)

1. **Tombol Wizard Otomatis Hilang Setelah Instalasi Berhasil**:
   - Begitu keempat layanan utama (**SSH, VLESS, VMess, Trojan**) selesai diinstal, halaman Wizard dinonaktifkan dan tombol Wizard dihilangkan sepenuhnya dari header agar tampilan bersih dan aman.
2. **Tombol Menu di Kiri Atas (`☰` Slide Drawer — Tidak Memenuhi Layar)**:
   - Navigasi dipindahkan ke tombol menu di sudut kiri atas dengan 5 menu terstruktur:
     - **📊 Menu 1 — Dashboard**: Menampilkan **Server Network Usage** (Total Traffic, Download, Upload `vnStat`, Host/IP, Mode Jaringan) serta **User Statistik** (Jumlah akun per protokol, IP client online, dan tabel status trafik user).
     - **➕ Menu 2 — Buat Akun (Simple)**: Form pembuatan akun ringkas (SSH WS, VLESS, VMess, Trojan, Trial) beserta output Link URI/Payload 1-klik, QR Code, dan daftar akun.
     - **📜 Menu 3 — Log Server**: Pemantau log real-time untuk Xray Access, Nginx Reverse Proxy (`CF-Connecting-IP`), SSH-WS Proxy, Cloudflared, dan Daemon Monitor beserta fitur Clear Log.
     - **🖥️ Menu 4 — Sistem**: Menampilkan **Resource Usage** dengan indikator bar (*CPU Usage*, *Memory/RAM*, *Storage Disk*, *Uptime*) serta **System Informasi** (OS, Kernel, Arsitektur, Hostname) dan kontrol Restart Daemon Service.
     - **⚙️ Menu 5 — Setting (Banner SSH & Backup/Restore)**: Editor langsung untuk mengubah `/etc/jibsz/banner.txt` serta fitur pembuatan, pengunduhan, dan pemulihan (*Restore*) file backup `.tar.gz`.
3. **Akses Publik Tanpa Login & Hapus Akun Khusus CLI**:
   - Halaman utama dapat diakses semua orang tanpa login untuk membuat akun dan memantau seluruh proses.
   - Fitur hapus akun yang sudah ada dikunci di Web Panel (`HTTP 403 Forbidden`) dan **hanya dapat dilakukan melalui terminal CLI** (`menu`, `menu-ssh`, `menu-vmess`, `menu-vless`, `menu-trojan`).

---

## ⚡ One-Liner Command Instalasi (GitHub `mohamadrojib278`)

Jalankan perintah berikut sebagai `root` pada VPS Ubuntu 22.04 LTS atau Debian 12 Anda:

```bash
apt update && apt install -y wget curl git unzip && wget -O setup.sh https://raw.githubusercontent.com/mohamadrojib278/jibsz-tunnel-autoinstall/main/setup.sh && chmod +x setup.sh && bash setup.sh
```

Atau langsung menentukan Custom Port Web Panel:

```bash
bash setup.sh --port 8080
```

---

## ☁️ Pengaturan Cloudflare Zero Trust Dashboard (Khusus Mode VPS NAT)

1. Buka **[Cloudflare Zero Trust Dashboard](https://one.dash.cloudflare.com/)** -> **Networks** -> **Tunnels** -> **Create a tunnel**.
2. Salin **Tunnel Token** (`eyJhIjoi...`) ke halaman Web Wizard Instalasi Jibsz saat pertama kali dijalankan.
3. Pada tab **Public Hostname**, arahkan domain/subdomain Anda ke Service Type **`HTTP`** dengan URL **`localhost:80`** (`http://localhost:80`).
4. Pastikan fitur **WebSockets** dan **gRPC** pada dashboard Cloudflare DNS -> Network berstatus **ON**.
