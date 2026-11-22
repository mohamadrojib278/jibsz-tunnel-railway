/* ============================================================================
   JIBSZ TUNNELING / J1BTNL - FRONTEND CONTROLLER V4.6 (app.js)
   Fitur: - Menampilkan Status Akun 'Blocked' (✕ Blocked) secara Real-Time ketika
            akun melebihi batas Devices Limit (ip_limit) atau Bandwidth Limit (quota_gb)
          - Tata Letak Dashboard 2 Baris x 2 Kolom (Server Network Usage) &
            Tabel All Accounts 5 Kolom (User, Status, Usage, Expired, Action)
          - Manage Modal hanya menampilkan 1 protokol terpilih & 4 tombol aksi
   ============================================================================ */

let pollInterval = null;
let selectedMode = "public";
window.accountsList = [];
window.accountsCache = {};
window.copyRegistry = {};
window.currentManagedAccountId = null;
window.isAdmin = false;
window.adminUsername = "";
window.defaultPublicIpLimit = 4;
window.defaultPublicQuotaGb = 0;

function getCurrentOpenedHostname() {
  return window.location.hostname || window.location.host || "";
}

function updateTopEndpointBadge() {
  const topEp = document.getElementById("top-endpoint-text");
  const currentHost = getCurrentOpenedHostname();
  if (topEp && currentHost) {
    topEp.textContent = currentHost;
  }
}

function copyActiveHostname() {
  const currentHost = getCurrentOpenedHostname();
  copyTextSafe(currentHost, "Hostname");
}

function applyAdminUiState(authData) {
  const isAdm = Boolean(authData && authData.is_admin);
  window.isAdmin = isAdm;
  window.adminUsername = (authData && authData.username) ? String(authData.username) : "";
  if (authData && authData.default_ip_limit !== undefined) {
    window.defaultPublicIpLimit = Number(authData.default_ip_limit) || 4;
  }
  if (authData && authData.default_quota_gb !== undefined) {
    window.defaultPublicQuotaGb = Number(authData.default_quota_gb) || 0;
  }

  document.body.classList.toggle("is-admin", isAdm);

  const labelEl = document.getElementById("admin-username-label");
  if (labelEl) labelEl.textContent = window.adminUsername || "admin";

  const newUserInput = document.getElementById("admin-new-user");
  if (newUserInput && window.adminUsername) newUserInput.value = window.adminUsername;

  const ipInput = document.getElementById("acc-ip-limit");
  const quotaInput = document.getElementById("acc-quota-gb");
  if (ipInput) {
    ipInput.readOnly = !isAdm;
    ipInput.disabled = !isAdm;
    ipInput.classList.toggle("locked-field", !isAdm);
    if (!isAdm) ipInput.value = window.defaultPublicIpLimit;
  }
  if (quotaInput) {
    quotaInput.readOnly = !isAdm;
    quotaInput.disabled = !isAdm;
    quotaInput.classList.toggle("locked-field", !isAdm);
    if (!isAdm) quotaInput.value = window.defaultPublicQuotaGb;
  }

  const defIpEl = document.getElementById("setting-default-ip");
  const defQuotaEl = document.getElementById("setting-default-quota");
  if (defIpEl) defIpEl.value = window.defaultPublicIpLimit;
  if (defQuotaEl) defQuotaEl.value = window.defaultPublicQuotaGb;

  if (!isAdm) {
    const logsPane = document.getElementById("pane-logs");
    const settingsPane = document.getElementById("pane-settings");
    if ((logsPane && logsPane.style.display !== "none") || (settingsPane && settingsPane.style.display !== "none")) {
      switchMenuV2("dashboard", "Dashboard");
    }
  }
}

async function refreshAuthState() {
  try {
    const res = await fetch("/api/auth/me");
    if (!res.ok) return;
    const data = await res.json();
    applyAdminUiState(data);
    renderRegisteredAccountsTable();
    renderCreatePageAccountsTable();
  } catch (e) {
    console.error("Auth check error:", e);
  }
}

function openAdminLoginModal() {
  toggleDrawer(false);
  const errEl = document.getElementById("admin-login-error");
  if (errEl) {
    errEl.style.display = "none";
    errEl.textContent = "";
  }
  const passEl = document.getElementById("admin-login-pass");
  if (passEl) passEl.value = "";
  const overlay = document.getElementById("admin-login-overlay");
  if (overlay) overlay.classList.add("open");
  const userEl = document.getElementById("admin-login-user");
  if (userEl) setTimeout(() => userEl.focus(), 60);
}

function closeAdminLoginModal() {
  const overlay = document.getElementById("admin-login-overlay");
  if (overlay) overlay.classList.remove("open");
}

function handleAdminLoginOverlayClick(event) {
  if (event.target && event.target.id === "admin-login-overlay") {
    closeAdminLoginModal();
  }
}

async function submitAdminLogin(event) {
  event.preventDefault();
  const userEl = document.getElementById("admin-login-user");
  const passEl = document.getElementById("admin-login-pass");
  const errEl = document.getElementById("admin-login-error");
  const btn = document.getElementById("btn-admin-login-submit");

  const username = userEl ? userEl.value.trim() : "";
  const password = passEl ? passEl.value : "";

  if (btn) btn.disabled = true;
  if (errEl) errEl.style.display = "none";

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (errEl) {
        errEl.textContent = data.detail || "Login admin gagal.";
        errEl.style.display = "block";
      }
      return;
    }
    applyAdminUiState(data);
    closeAdminLoginModal();
    showToast(`🔓 Login Admin berhasil (${data.username})!`);
    loadOverviewAndSystem();
    loadAccountsData();
  } catch (err) {
    if (errEl) {
      errEl.textContent = "Gagal terhubung ke server.";
      errEl.style.display = "block";
    }
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function logoutAdmin() {
  try {
    await fetch("/api/auth/logout", { method: "POST" });
  } catch (e) {}
  applyAdminUiState({
    is_admin: false,
    username: "",
    default_ip_limit: window.defaultPublicIpLimit,
    default_quota_gb: window.defaultPublicQuotaGb,
  });
  toggleDrawer(false);
  closeManageModal();
  showToast("🔒 Logout Admin berhasil. Kembali ke mode publik.");
  loadOverviewAndSystem();
  loadAccountsData();
}

async function savePublicDefaultLimits() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat mengubah default limit.");
    return;
  }
  const ipLimit = parseInt(document.getElementById("setting-default-ip").value || "4", 10);
  const quotaGb = parseFloat(document.getElementById("setting-default-quota").value || "0");
  const res = await fetch("/api/settings/public-limits", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_ip_limit: ipLimit, default_quota_gb: quotaGb }),
  });
  const data = await res.json();
  if (!res.ok) {
    showToast(data.detail || "Gagal menyimpan default limit.");
    return;
  }
  window.defaultPublicIpLimit = data.default_ip_limit;
  window.defaultPublicQuotaGb = data.default_quota_gb;
  showToast("✅ Default limit akun publik berhasil disimpan!");
}

async function submitChangeAdminPassword(event) {
  event.preventDefault();
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat mengubah kredensial.");
    return;
  }
  const newUsername = document.getElementById("admin-new-user").value.trim();
  const currentPassword = document.getElementById("admin-cur-pass").value;
  const newPassword = document.getElementById("admin-new-pass").value;

  const res = await fetch("/api/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_username: newUsername,
      new_password: newPassword,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    showToast(data.detail || "Gagal memperbarui password admin.");
    return;
  }
  document.getElementById("admin-cur-pass").value = "";
  document.getElementById("admin-new-pass").value = "";
  applyAdminUiState({
    is_admin: true,
    username: data.username,
    default_ip_limit: window.defaultPublicIpLimit,
    default_quota_gb: window.defaultPublicQuotaGb,
  });
  showToast("✅ Username & password admin berhasil diperbarui!");
}

/* ============================================================================
   HTTP & HTTPS SAFE CLIPBOARD ENGINE (100% WORK DI http://IP:PORT ANDROID)
   ============================================================================ */
function fallbackCopyViaTextarea(text) {
  const modalCard = document.getElementById("manage-modal-card");
  const modalOverlay = document.getElementById("manage-modal-overlay");
  const isModalOpen = modalOverlay && modalOverlay.classList.contains("open");
  const container = (isModalOpen && modalCard) ? modalCard : document.body;

  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText =
    "position:fixed;top:0;left:0;width:2em;height:2em;padding:0;border:none;outline:none;box-shadow:none;background:transparent;opacity:0.01;z-index:99999;";
  container.appendChild(ta);
  ta.focus();
  ta.select();
  try {
    ta.setSelectionRange(0, text.length);
  } catch (e) {}

  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch (err) {
    ok = false;
  }
  container.removeChild(ta);
  return ok;
}

function copyTextSafe(text, label = "Config") {
  if (!text) {
    showToast("Tidak ada teks untuk disalin.");
    return;
  }
  const str = String(text);

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(str).then(() => {
      showToast(`✅ ${label} berhasil disalin!`);
    }).catch(() => {
      fallbackCopyViaTextarea(str);
      showToast(`✅ ${label} berhasil disalin!`);
    });
  } else {
    fallbackCopyViaTextarea(str);
    showToast(`✅ ${label} berhasil disalin!`);
  }
}

function copyText(text, label = "Config") {
  copyTextSafe(text, label);
}

function copyFromRegistry(regKey, label = "Config") {
  const val = window.copyRegistry[regKey];
  if (!val) {
    showToast("Data config tidak ditemukan.");
    return;
  }
  copyTextSafe(val, label);
}

function showToast(msg) {
  let toast = document.getElementById("jibsz-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "jibsz-toast";
    toast.style.cssText =
      "position:fixed;bottom:22px;left:50%;transform:translateX(-50%);background:linear-gradient(135deg,#00f2fe,#a855f7);" +
      "color:#040711;font-weight:800;font-size:0.78rem;padding:9px 18px;border-radius:999px;z-index:99999;" +
      "box-shadow:0 8px 25px rgba(0,0,0,0.75);max-width:92vw;text-align:center;pointer-events:none;";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.style.display = "block";
  clearTimeout(window._toastTimer);
  window._toastTimer = setTimeout(() => {
    toast.style.display = "none";
  }, 2400);
}

function formatBytes(bytes) {
  const b = Number(bytes || 0);
  if (b <= 0) return "0 B";
  if (b < 1024) return `${b} B`;
  if (b < 1024 ** 2) return `${(b / 1024).toFixed(2)} KB`;
  if (b < 1024 ** 3) return `${(b / (1024 ** 2)).toFixed(2)} MB`;
  return `${(b / (1024 ** 3)).toFixed(2)} GB`;
}

/* ============================================================================
   ANIMATED CONSTELLATION BACKGROUND CANVAS
   ============================================================================ */
function initConstellationCanvas() {
  const canvas = document.getElementById("constellation-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let w = (canvas.width = window.innerWidth);
  let h = (canvas.height = window.innerHeight);

  const count = Math.min(48, Math.max(22, Math.floor((w * h) / 22000)));
  const nodes = [];
  for (let i = 0; i < count; i++) {
    nodes.push({
      x: Math.random() * w,
      y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.35,
      vy: (Math.random() - 0.5) * 0.35,
      r: Math.random() * 1.6 + 0.8,
    });
  }

  window.addEventListener("resize", () => {
    w = canvas.width = window.innerWidth;
    h = canvas.height = window.innerHeight;
  });

  function draw() {
    ctx.clearRect(0, 0, w, h);
    for (let i = 0; i < nodes.length; i++) {
      const p = nodes[i];
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0 || p.x > w) p.vx *= -1;
      if (p.y < 0 || p.y > h) p.vy *= -1;

      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(148, 163, 184, 0.38)";
      ctx.fill();

      for (let j = i + 1; j < nodes.length; j++) {
        const q = nodes[j];
        const dx = p.x - q.x;
        const dy = p.y - q.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 130) {
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = `rgba(100, 116, 139, ${(1 - dist / 130) * 0.18})`;
          ctx.lineWidth = 0.8;
          ctx.stroke();
        }
      }
    }
    requestAnimationFrame(draw);
  }
  requestAnimationFrame(draw);
}

/* ============================================================================
   DRAWER & PROTOCOL FIELD TOGGLES
   ============================================================================ */
function toggleDrawer(forceState) {
  const drawer = document.getElementById("sidebar-drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  if (!drawer || !backdrop) return;

  const isOpen = typeof forceState === "boolean" ? forceState : !drawer.classList.contains("open");
  if (isOpen) {
    drawer.classList.add("open");
    backdrop.classList.add("open");
  } else {
    drawer.classList.remove("open");
    backdrop.classList.remove("open");
  }
}

function toggleProtocolPasswordField() {
  const protoSel = document.getElementById("acc-protocol");
  const passGroup = document.getElementById("group-ssh-password");
  const passInput = document.getElementById("acc-secret");
  if (!protoSel || !passGroup || !passInput) return;

  if (protoSel.value === "ssh") {
    passGroup.style.display = "block";
    passInput.required = true;
  } else {
    passGroup.style.display = "none";
    passInput.required = false;
    passInput.value = "";
  }
}

function switchMenuV2(menuId, titleText) {
  if ((menuId === "logs" || menuId === "settings") && !window.isAdmin) {
    showToast("🔒 Menu ini khusus Admin. Silakan login Admin.");
    openAdminLoginModal();
    return;
  }
  document.querySelectorAll(".tab-pane").forEach(el => {
    el.style.display = "none";
  });
  document.querySelectorAll(".drawer-item").forEach(el => {
    el.classList.remove("active");
  });

  const pane = document.getElementById(`pane-${menuId}`);
  const navBtn = document.getElementById(`nav-${menuId}`);
  const headerTitle = document.getElementById("active-menu-title");

  if (pane) pane.style.display = "block";
  if (navBtn) navBtn.classList.add("active");
  if (headerTitle && titleText) headerTitle.textContent = titleText;

  toggleDrawer(false);
  window.scrollTo({ top: 0, behavior: "smooth" });

  if (menuId === "dashboard") {
    loadOverviewAndSystem();
    loadAccountsData();
  } else if (menuId === "create") {
    toggleProtocolPasswordField();
    loadAccountsData();
  } else if (menuId === "logs") {
    loadServerLogs();
  } else if (menuId === "system") {
    loadOverviewAndSystem();
  } else if (menuId === "settings") {
    loadBannerAndBackups();
  }
}

/* ============================================================================
   WIZARD INSTALASI
   ============================================================================ */
function selectInstallMode(mode) {
  selectedMode = mode;
  const cardPub = document.getElementById("mode-card-public");
  const cardNat = document.getElementById("mode-card-nat");
  const groupEmail = document.getElementById("group-email");
  const groupCfToken = document.getElementById("group-cf-token");
  const hintBox = document.getElementById("nat-hint-box");
  if (!cardPub || !cardNat) return;

  if (mode === "nat") {
    cardNat.classList.add("selected");
    cardPub.classList.remove("selected");
    groupEmail.style.display = "none";
    groupCfToken.style.display = "block";
    hintBox.style.display = "block";
  } else {
    cardPub.classList.add("selected");
    cardNat.classList.remove("selected");
    groupEmail.style.display = "block";
    groupCfToken.style.display = "none";
    hintBox.style.display = "none";
  }
}

async function startWizardInstallation() {
  const domain = document.getElementById("input-domain").value.trim();
  const email = document.getElementById("input-email").value.trim();
  const cfToken = document.getElementById("input-cf-token").value.trim();

  if (!domain) {
    alert("Masukkan Domain / Subdomain terlebih dahulu!");
    return;
  }
  if (selectedMode === "nat" && !cfToken) {
    alert("Masukkan Cloudflare Tunnel Token (eyJhIjoi...) untuk mode VPS NAT!");
    return;
  }

  const btn = document.getElementById("btn-start-install");
  btn.disabled = true;
  btn.textContent = "⏳ Memulai Instalasi...";

  try {
    const res = await fetch("/api/install/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: selectedMode, domain, email, cf_token: cfToken }),
    });
    const data = await res.json();
    if (!res.ok) {
      alert(data.detail || "Gagal memulai instalasi.");
      btn.disabled = false;
      btn.textContent = "🚀 Mulai Instalasi Sekarang";
      return;
    }
    showToast("Instalasi berjalan di background!");
    pollInstallationProgress();
    if (!pollInterval) pollInterval = setInterval(pollInstallationProgress, 2000);
  } catch (err) {
    alert("Kesalahan koneksi: " + err);
    btn.disabled = false;
  }
}

async function retrySingleComponent(compId) {
  showToast(`Retry komponen: ${compId}...`);
  await fetch("/api/install/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ component_id: compId }),
  });
  pollInstallationProgress();
}

function mapStatusBadge(status) {
  const s = (status || "pending").toLowerCase();
  if (s === "success") return `<span class="badge badge-active">Berhasil</span>`;
  if (s === "installing") return `<span class="badge badge-online">Installing</span>`;
  if (s === "failed") return `<span class="badge badge-locked">Gagal</span>`;
  return `<span class="badge">Pending</span>`;
}

async function pollInstallationProgress() {
  const container = document.getElementById("components-list");
  if (!container) return;

  try {
    const res = await fetch("/api/install/status");
    const data = await res.json();
    if (!data || !Array.isArray(data.components)) return;

    container.innerHTML = data.components.map((c, idx) => {
      const retryBtn = c.status === "failed"
        ? `<button class="btn btn-warning btn-sm" onclick="retrySingleComponent('${c.component_id}')">🔄 Retry</button>`
        : "";
      return `
        <div class="component-item">
          <div class="component-info">
            <h4>${idx + 1}. ${c.name}</h4>
            <p>${c.message || ""}</p>
          </div>
          <div style="display:flex;align-items:center;gap:6px;">
            ${mapStatusBadge(c.status)}
            ${retryBtn}
          </div>
        </div>
      `;
    }).join("");

    const hasFailed = data.components.some(c => c.status === "failed");
    const retryAllBtn = document.getElementById("btn-retry-all-failed");
    if (retryAllBtn) retryAllBtn.style.display = hasFailed ? "inline-flex" : "none";

    const logBox = document.getElementById("install-log-box");
    if (logBox && data.logs) {
      logBox.textContent = data.logs;
      logBox.scrollTop = logBox.scrollHeight;
    }

    if (data.is_installed) {
      window.location.href = "/dashboard";
    }
  } catch (e) {
    console.error("Polling error:", e);
  }
}

/* ============================================================================
   OVERVIEW: USER STATISTICS, SYNCHRONIZED 4-PERIOD TRAFFIC, & SYSTEM RESOURCES
   ============================================================================ */
async function loadOverviewAndSystem() {
  if (window._overviewLoading) return;
  window._overviewLoading = true;
  try {
    const res = await fetch("/api/system/overview");
    if (!res.ok) return;
    const data = await res.json();
    if (!data || !data.system || !data.network) return;

    const s = data.system;
    const n = data.network;
    const bw = s.bandwidth || {};
    const ust = data.user_statistics || {};

    updateTopEndpointBadge();

    const elTotNum = document.getElementById("ust-total-num");
    if (elTotNum) {
      elTotNum.textContent = ust.total_users ?? data.total_accounts ?? 0;
      document.getElementById("ust-total-sub").innerHTML =
        `SSH: <b>${ust.total_ssh ?? 0}</b> • Xray: <b>${ust.total_xray ?? 0}</b>`;

      document.getElementById("ust-online-num").textContent = ust.online_users ?? 0;
      document.getElementById("ust-online-sub").innerHTML =
        `SSH: <b>${ust.online_ssh ?? 0}</b> • Xray: <b>${ust.online_xray ?? 0}</b>`;

      document.getElementById("ust-block-num").textContent = ust.blocked_users ?? 0;
      document.getElementById("ust-block-sub").innerHTML =
        `SSH: <b>${ust.blocked_ssh ?? 0}</b> • Xray: <b>${ust.blocked_xray ?? 0}</b>`;
    }

    const elTodayTot = document.getElementById("bw-today-tot");
    if (elTodayTot && bw.today) {
      elTodayTot.textContent = bw.today.total || "0 B";
      document.getElementById("bw-today-up").textContent = bw.today.up || "0 B";
      document.getElementById("bw-today-dn").textContent = bw.today.down || "0 B";

      document.getElementById("bw-month-tot").textContent = (bw.month && bw.month.total) || "0 B";
      document.getElementById("bw-month-up").textContent = (bw.month && bw.month.up) || "0 B";
      document.getElementById("bw-month-dn").textContent = (bw.month && bw.month.down) || "0 B";

      document.getElementById("bw-year-tot").textContent = (bw.year && bw.year.total) || "0 B";
      document.getElementById("bw-year-up").textContent = (bw.year && bw.year.up) || "0 B";
      document.getElementById("bw-year-dn").textContent = (bw.year && bw.year.down) || "0 B";

      document.getElementById("bw-all-tot").textContent = (bw.all_time && bw.all_time.total) || "0 B";
      document.getElementById("bw-all-up").textContent = (bw.all_time && bw.all_time.up) || "0 B";
      document.getElementById("bw-all-dn").textContent = (bw.all_time && bw.all_time.down) || "0 B";

      document.getElementById("bw-updated-at").textContent = `Updated: ${bw.updated_at || "-"}`;
    }

    const sysCpu = document.getElementById("sys-cpu-val");
    if (sysCpu) {
      sysCpu.textContent = `${s.cpu_percent}% (${s.cpu_cores} Core)`;
      document.getElementById("sys-cpu-bar").style.width = `${Math.min(100, s.cpu_percent)}%`;
      document.getElementById("sys-load-sub").textContent = `Load: ${s.load_avg}`;

      document.getElementById("sys-ram-val").textContent = `${s.ram_used_mb} / ${s.ram_total_mb} MB (${s.ram_percent}%)`;
      document.getElementById("sys-ram-bar").style.width = `${Math.min(100, s.ram_percent)}%`;

      document.getElementById("sys-disk-val").textContent = `${s.disk_used_gb} / ${s.disk_total_gb} GB (${s.disk_percent}%)`;
      document.getElementById("sys-disk-bar").style.width = `${Math.min(100, s.disk_percent)}%`;

      document.getElementById("sys-uptime-val").textContent = s.uptime;
      document.getElementById("sys-os-val").textContent = s.os;
      document.getElementById("sys-kernel-val").textContent = `${s.kernel} (${s.arch})`;
      document.getElementById("sys-host-val").textContent = s.hostname;
      document.getElementById("sys-domain-val").textContent = `${n.domain} (${n.public_ip}:${n.panel_port})`;
    }

    const svcTbody = document.getElementById("service-status-tbody");
    if (svcTbody && Array.isArray(data.services)) {
      svcTbody.innerHTML = data.services.map(item => `
        <tr>
          <td><strong>${item.name.toUpperCase()}</strong></td>
          <td>
            <span class="badge ${item.status === 'running' ? 'badge-running' : 'badge-stopped'}">
              ${item.status.toUpperCase()}
            </span>
          </td>
          <td class="admin-only" style="text-align:right;">
            <button type="button" class="btn-manage" onclick="restartService('${item.name}')">🔄 Restart</button>
          </td>
        </tr>
      `).join("");
    }
  } catch (err) {
    console.error("Overview error:", err);
  } finally {
    window._overviewLoading = false;
  }
}

async function restartService(svcName) {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat me-restart service.");
    return;
  }
  showToast(`Me-restart ${svcName}...`);
  const res = await fetch("/api/service/restart", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ service: svcName }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.detail || "Akses ditolak.");
    return;
  }
  loadOverviewAndSystem();
}

/* ============================================================================
   REGISTERED ACCOUNTS LOADER & SINGLE-SCREEN TABLE RENDERER (5 COLUMNS)
   ============================================================================ */
function registerAccountCopyKeys(acc) {
  const d = acc.connection_details || {};
  const id = acc.id;
  window.copyRegistry[`acc_${id}_secret`] = acc.secret || "";
  window.copyRegistry[`acc_${id}_uri_tls`] = d.uri_tls || "";
  window.copyRegistry[`acc_${id}_uri_ntls`] = d.uri_ntls || "";
  window.copyRegistry[`acc_${id}_uri_grpc`] = d.uri_grpc || "";
  window.copyRegistry[`acc_${id}_ssh_tls`] = d.ssh_format_tls || "";
  window.copyRegistry[`acc_${id}_ssh_ntls`] = d.ssh_format_ntls || "";
  window.copyRegistry[`acc_${id}_ssh_trace`] = d.ssh_payload_trace || "";
  window.copyRegistry[`acc_${id}_ssh_ws`] = d.ssh_payload_ws || "";
}

async function loadAccountsData(silent = false) {
  if (silent && window._accountsLoading) return;
  window._accountsLoading = true;
  try {
    const res = await fetch("/api/accounts");
    if (!res.ok) return;
    const data = await res.json();
    window.accountsList = (data && Array.isArray(data.accounts)) ? data.accounts : [];
    window.accountsCache = {};
    window.accountsList.forEach(a => {
      window.accountsCache[a.id] = a;
      registerAccountCopyKeys(a);
    });

    renderRegisteredAccountsTable();
    renderCreatePageAccountsTable();

    if (window.currentManagedAccountId && window.accountsCache[window.currentManagedAccountId]) {
      populateManageModalFields(window.accountsCache[window.currentManagedAccountId], silent);
    }
  } catch (e) {
    console.error("Load accounts error:", e);
  } finally {
    window._accountsLoading = false;
  }
}

function formatStatusBadgeHtml(statusRaw) {
  const st = String(statusRaw || "active").toLowerCase();
  if (st === "active") {
    return `<span class="status-pill st-active">✓ Active</span>`;
  }
  if (st === "expired") {
    return `<span class="status-pill st-expired">✕ Expired</span>`;
  }
  return `<span class="status-pill st-locked">✕ Blocked</span>`;
}

function formatStatusLabelModal(statusRaw) {
  const st = String(statusRaw || "active").toLowerCase();
  if (st === "active") return "Active";
  if (st === "expired") return "Expired";
  return "Blocked";
}

function buildAccountRowHtml(a) {
  const isSsh = a.protocol === "ssh";
  const svcBadge = isSsh
    ? `<span class="svc-badge svc-ssh">SSH</span>`
    : `<span class="svc-badge svc-xray">${a.protocol.toUpperCase()}</span>`;

  const statusHtml = formatStatusBadgeHtml(a.status);

  const onlineCount = Number(a.connected_ip_count || 0);
  const devHtml = onlineCount > 0
    ? `<span class="dev-mini on"><span class="dev-dot-on"></span>Online (${onlineCount})</span>`
    : `<span class="dev-mini"><span class="dev-dot-off"></span>Offline</span>`;

  const upB = Number(a.up_bytes || 0);
  const dnB = Number(a.down_bytes || 0);
  const totB = upB + dnB;
  const usageHtml = totB > 0
    ? `<div class="usage-cell-main">${formatBytes(totB)}</div>
       <div class="usage-cell-sub"><span style="color:#38bdf8;">↑${formatBytes(upB)}</span> <span style="color:#10b981;">↓${formatBytes(dnB)}</span></div>`
    : `<div class="usage-cell-zero">0 B</div>`;

  const expShort = (a.exp_display || "").split(" ")[0] || a.exp_display;

  return `
    <tr>
      <td class="col-user">
        <div class="user-cell-wrap">
          ${svcBadge}
          <span class="user-name-line">${a.username}</span>
        </div>
      </td>
      <td class="col-status">
        <div class="status-stack">
          ${statusHtml}
          ${devHtml}
        </div>
      </td>
      <td class="col-usage">${usageHtml}</td>
      <td class="col-exp"><span class="exp-cell-text">${expShort}</span></td>
      <td class="col-act">
        <button type="button" class="btn-manage" onclick="openManageModal(${a.id})">${window.isAdmin ? "Manage" : "Detail"}</button>
      </td>
    </tr>
  `;
}

function renderRegisteredAccountsTable() {
  const tbody = document.getElementById("dashboard-users-tbody");
  if (!tbody) return;

  const filterEl = document.getElementById("filter-service-select");
  const searchEl = document.getElementById("search-username-input");
  const svcFilter = filterEl ? filterEl.value : "all";
  const query = searchEl ? searchEl.value.trim().toLowerCase() : "";

  const filtered = window.accountsList.filter(a => {
    if (svcFilter === "xray" && a.protocol === "ssh") return false;
    if (svcFilter !== "all" && svcFilter !== "xray" && a.protocol !== svcFilter) return false;
    if (query && !a.username.toLowerCase().includes(query)) return false;
    return true;
  });

  if (!filtered.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#64748b;padding:16px;">Tidak ada akun yang cocok.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.map(buildAccountRowHtml).join("");
}

function renderCreatePageAccountsTable() {
  const tbody = document.getElementById("crud-accounts-tbody");
  if (!tbody) return;

  if (!window.accountsList.length) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center;color:#64748b;padding:16px;">Belum ada akun terdaftar.</td></tr>`;
    return;
  }
  tbody.innerHTML = window.accountsList.map(buildAccountRowHtml).join("");
}

/* ============================================================================
   MANAGE MODAL POPUP (COPY CONFIG, RENEW, EDIT RULES, RESET USAGE)
   ============================================================================ */
function buildConfigRowsHtml(acc) {
  const id = acc.id;
  const d = acc.connection_details || {};
  const protoUpper = acc.protocol.toUpperCase();

  if (acc.protocol === "ssh") {
    return `
      <div class="mm-copy-item">
        <label>SSH TLS Port 443 (host:port@username:password):</label>
        <div class="mm-copy-row">
          <span class="mm-copy-text">${d.ssh_format_tls}</span>
          <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_ssh_tls', 'SSH TLS 443')">📋 Copy</button>
        </div>
      </div>
      <div class="mm-copy-item">
        <label>SSH Non-TLS Port 80 (host:port@username:password):</label>
        <div class="mm-copy-row">
          <span class="mm-copy-text">${d.ssh_format_ntls}</span>
          <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_ssh_ntls', 'SSH Non-TLS 80')">📋 Copy</button>
        </div>
      </div>
      <div class="mm-copy-item">
        <label>Payload WSS / Dark++ (/cdn-cgi/trace + GET-WSS):</label>
        <div class="mm-copy-row">
          <span class="mm-copy-text">${d.ssh_payload_trace}</span>
          <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_ssh_trace', 'Payload Dark++')">📋 Copy</button>
        </div>
      </div>
      <div class="mm-copy-item">
        <label>Payload WS Standar (/ssh-ws):</label>
        <div class="mm-copy-row">
          <span class="mm-copy-text">${d.ssh_payload_ws}</span>
          <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_ssh_ws', 'Payload WS')">📋 Copy</button>
        </div>
      </div>
    `;
  }

  return `
    <div class="mm-copy-item">
      <label>Link WS TLS Port 443 (${protoUpper}):</label>
      <div class="mm-copy-row">
        <span class="mm-copy-text">${d.uri_tls}</span>
        <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_uri_tls', '${protoUpper} TLS 443')">📋 Copy</button>
      </div>
    </div>
    <div class="mm-copy-item">
      <label>Link WS Non-TLS Port 80 (${protoUpper}):</label>
      <div class="mm-copy-row">
        <span class="mm-copy-text">${d.uri_ntls}</span>
        <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_uri_ntls', '${protoUpper} Non-TLS 80')">📋 Copy</button>
      </div>
    </div>
    <div class="mm-copy-item">
      <label>Link gRPC TLS 443 (${d.service_name_grpc}):</label>
      <div class="mm-copy-row">
        <span class="mm-copy-text">${d.uri_grpc}</span>
        <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_uri_grpc', '${protoUpper} gRPC')">📋 Copy</button>
      </div>
    </div>
    <div class="mm-copy-item">
      <label>UUID (${protoUpper}):</label>
      <div class="mm-copy-row">
        <span class="mm-copy-text">${acc.secret}</span>
        <button type="button" class="btn btn-primary btn-sm" onclick="copyFromRegistry('acc_${id}_secret', 'UUID ${protoUpper}')">📋 Copy UUID</button>
      </div>
    </div>
  `;
}

function populateManageModalFields(acc, keepSubpanels = false) {
  registerAccountCopyKeys(acc);
  const isSsh = acc.protocol === "ssh";
  const protoUpper = acc.protocol.toUpperCase();

  document.getElementById("mm-username").textContent = acc.username;
  const badgeEl = document.getElementById("mm-svc-badge");
  badgeEl.className = isSsh ? "svc-badge svc-ssh" : "svc-badge svc-xray";
  badgeEl.textContent = protoUpper;

  const statusCap = formatStatusLabelModal(acc.status);
  const statusEl = document.getElementById("mm-status");
  statusEl.textContent = statusCap;
  statusEl.style.color = statusCap === "Active" ? "#34d399" : "#fb7185";
  document.getElementById("mm-secret").textContent = acc.secret;
  document.getElementById("mm-active-dev").textContent = `${acc.connected_ip_count || 0} Online`;
  document.getElementById("mm-dev-limit").textContent = `${acc.ip_limit}`;

  const qGb = Number(acc.quota_gb || 0);
  document.getElementById("mm-bw-limit").textContent = qGb <= 0 ? "Unlimited" : `${qGb} GB`;

  const upB = Number(acc.up_bytes || 0);
  const dnB = Number(acc.down_bytes || 0);
  document.getElementById("mm-cum-usage").textContent = formatBytes(upB + dnB);
  document.getElementById("mm-up-down").innerHTML =
    `<span class="tx-part">↑ ${formatBytes(upB)}</span> / <span class="rx-part">↓ ${formatBytes(dnB)}</span>`;

  document.getElementById("mm-issued").textContent = acc.created_at || "-";
  document.getElementById("mm-expired").textContent = acc.exp_display || "-";
  document.getElementById("mm-protocols").textContent = isSsh ? "SSH WEBSOCKET" : protoUpper;

  const ips = acc.connected_ips || [];
  document.getElementById("mm-active-ips").innerHTML = ips.length
    ? ips.map(ip => `<span class="mm-ip-item">• ${ip}</span>`).join("")
    : `<span style="color:#64748b;">• Tidak ada IP aktif</span>`;

  document.getElementById("mm-subpanel-copy").innerHTML = buildConfigRowsHtml(acc);

  if (!keepSubpanels) {
    document.getElementById("mm-rule-ip").value = acc.ip_limit || 4;
    document.getElementById("mm-rule-quota").value = qGb;
    document.querySelectorAll(".mm-subpanel").forEach(el => el.classList.remove("active"));
  }
}

function openManageModal(accountId) {
  const acc = window.accountsCache[accountId];
  if (!acc) return;
  window.currentManagedAccountId = accountId;
  populateManageModalFields(acc, false);

  const overlay = document.getElementById("manage-modal-overlay");
  if (overlay) overlay.classList.add("open");
}

function closeManageModal() {
  window.currentManagedAccountId = null;
  const overlay = document.getElementById("manage-modal-overlay");
  if (overlay) overlay.classList.remove("open");
}

function handleModalOverlayClick(event) {
  if (event.target && event.target.id === "manage-modal-overlay") {
    closeManageModal();
  }
}

function toggleModalSubpanel(panelName) {
  if ((panelName === "renew" || panelName === "rules") && !window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat melakukan perubahan.");
    return;
  }
  const targetId = `mm-subpanel-${panelName}`;
  document.querySelectorAll(".mm-subpanel").forEach(el => {
    if (el.id === targetId) {
      el.classList.toggle("active");
    } else {
      el.classList.remove("active");
    }
  });
}

function handleModalCopyConfig() {
  const id = window.currentManagedAccountId;
  const acc = window.accountsCache[id];
  if (!acc) return;

  const copyPanel = document.getElementById("mm-subpanel-copy");
  document.querySelectorAll(".mm-subpanel").forEach(el => {
    if (el.id !== "mm-subpanel-copy") el.classList.remove("active");
  });
  if (copyPanel) copyPanel.classList.add("active");

  const primaryKey = acc.protocol === "ssh" ? `acc_${id}_ssh_tls` : `acc_${id}_uri_tls`;
  copyFromRegistry(primaryKey, `Config ${acc.protocol.toUpperCase()} (${acc.username})`);
}

async function submitModalRenew() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat melakukan Renew.");
    return;
  }
  const id = window.currentManagedAccountId;
  if (!id) return;
  const daysInput = document.getElementById("mm-renew-days");
  const addDays = parseInt(daysInput ? daysInput.value : "30", 10) || 30;

  const res = await fetch(`/api/accounts/${id}/renew`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ add_days: addDays }),
  });
  if (res.ok) {
    showToast(`✅ Masa aktif diperpanjang +${addDays} hari!`);
    await loadAccountsData(false);
    loadOverviewAndSystem();
  }
}

async function submitModalEditRules() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat mengubah Limit Device & Kuota.");
    return;
  }
  const id = window.currentManagedAccountId;
  if (!id) return;
  const ipLimit = parseInt(document.getElementById("mm-rule-ip").value || "2", 10);
  const quotaGb = parseFloat(document.getElementById("mm-rule-quota").value || "0");

  const res = await fetch(`/api/accounts/${id}/rules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ip_limit: ipLimit, quota_gb: quotaGb }),
  });
  if (res.ok) {
    showToast("✅ Rules diperbarui & akun telah diaktifkan kembali!");
    await loadAccountsData(false);
    loadOverviewAndSystem();
  }
}

async function handleModalResetUsage() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat me-reset usage.");
    return;
  }
  const id = window.currentManagedAccountId;
  const acc = window.accountsCache[id];
  if (!id || !acc) return;

  const res = await fetch(`/api/accounts/${id}/reset-usage`, {
    method: "POST",
  });
  if (res.ok) {
    showToast(`✅ Usage '${acc.username}' di-reset ke 0 B & status kembali Active!`);
    await loadAccountsData(true);
    loadOverviewAndSystem();
  }
}

async function handleModalDeleteAccount() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat menghapus akun.");
    return;
  }
  const id = window.currentManagedAccountId;
  const acc = window.accountsCache[id];
  if (!id || !acc) return;

  if (!confirm(`Hapus permanen akun '${acc.username}' (${acc.protocol.toUpperCase()})?`)) return;

  const res = await fetch(`/api/accounts/${id}`, {
    method: "DELETE",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast(data.detail || "Gagal menghapus akun.");
    return;
  }
  closeManageModal();
  showToast(`🗑️ Akun '${acc.username}' berhasil dihapus!`);
  await loadAccountsData(false);
  loadOverviewAndSystem();
}

/* ============================================================================
   MENU 2: BUAT AKUN BARU
   ============================================================================ */
function renderCreatedAccountBox(acc) {
  const box = document.getElementById("created-account-output");
  if (!box) return;
  registerAccountCopyKeys(acc);
  const d = acc.connection_details || {};
  box.style.display = "block";

  const qrHtml = d.qr_svg
    ? `<div style="margin-top:10px;text-align:center;">
         <img src="${d.qr_svg}" alt="QR" style="background:#fff;padding:6px;border-radius:10px;max-width:135px;" />
       </div>`
    : "";

  const secretLabel = acc.protocol === "ssh" ? "Password" : "UUID";

  box.innerHTML = `
    <div class="card-title">
      <span>✅ Akun Berhasil Dibuat (${acc.protocol.toUpperCase()} — ${acc.username})</span>
      <button type="button" class="btn-manage" onclick="openManageModal(${acc.id})">⚙️ Manage</button>
    </div>
    <div style="font-size:0.74rem;display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-bottom:10px;">
      <div>Host: <code>${d.host}</code></div>
      <div>Protokol: <code>${acc.protocol.toUpperCase()}</code></div>
      <div>${secretLabel}: <code>${acc.secret}</code></div>
      <div>Path: <code>${d.path_ws}</code></div>
      <div>Exp: <code>${acc.exp_display}</code></div>
    </div>
    ${buildConfigRowsHtml(acc)}
    ${qrHtml}
  `;
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function submitCreateAccount(event) {
  event.preventDefault();
  const protocol = document.getElementById("acc-protocol").value;
  const username = document.getElementById("acc-username").value.trim();
  const secretEl = document.getElementById("acc-secret");
  const secret = (protocol === "ssh" && secretEl) ? secretEl.value.trim() : "";
  const durationMode = document.getElementById("acc-duration-mode").value;
  const durationValue = parseInt(document.getElementById("acc-duration-val").value || "30", 10);
  const ipLimit = parseInt(document.getElementById("acc-ip-limit").value || "4", 10);
  const quotaGb = parseFloat(document.getElementById("acc-quota-gb").value || "0");

  if (protocol === "ssh" && !secret) {
    alert("Masukkan Password untuk membuat akun SSH!");
    return;
  }

  const res = await fetch("/api/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      protocol,
      username,
      secret,
      duration_mode: durationMode,
      duration_value: durationValue,
      ip_limit: ipLimit,
      quota_gb: quotaGb,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.detail || "Gagal membuat akun.");
    return;
  }
  showToast(`✅ Akun ${username} (${protocol.toUpperCase()}) berhasil dibuat!`);
  await loadAccountsData();
  loadOverviewAndSystem();
  renderCreatedAccountBox(data.account);
}

/* ============================================================================
   MENU 3: LOG SERVER
   ============================================================================ */
async function loadServerLogs() {
  const sel = document.getElementById("log-source-select");
  const box = document.getElementById("server-log-box");
  if (!sel || !box) return;

  const res = await fetch(`/api/logs?source=${encodeURIComponent(sel.value)}&lines=120`);
  const data = await res.json();
  box.textContent = data.content || "Log kosong.";
  box.scrollTop = box.scrollHeight;
}

async function clearServerLogs() {
  const sel = document.getElementById("log-source-select");
  if (!sel) return;
  await fetch(`/api/logs/clear?source=${encodeURIComponent(sel.value)}`, { method: "POST" });
  showToast("Log dibersihkan!");
  loadServerLogs();
}

/* ============================================================================
   MENU 5: SETTING BANNER SSH & BACKUP / RESTORE
   ============================================================================ */
async function loadBannerAndBackups() {
  const bannerInput = document.getElementById("ssh-banner-textarea");
  if (bannerInput) {
    const res = await fetch("/api/settings/banner");
    const data = await res.json();
    bannerInput.value = data.banner_html || "";
  }
  loadBackupList();
}

async function saveSshBanner() {
  if (!window.isAdmin) {
    showToast("🔒 Hanya Admin yang dapat mengubah Banner SSH.");
    return;
  }
  const bannerInput = document.getElementById("ssh-banner-textarea");
  if (!bannerInput) return;
  const res = await fetch("/api/settings/banner", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ banner_html: bannerInput.value }),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    showToast(data.detail || "Gagal menyimpan banner SSH.");
    return;
  }
  showToast("✅ Banner SSH berhasil disimpan & diterapkan!");
}

async function loadBackupList() {
  const tbody = document.getElementById("backup-list-tbody");
  if (!tbody) return;
  const res = await fetch("/api/settings/backups");
  const data = await res.json();

  if (!data.backups || !data.backups.length) {
    tbody.innerHTML = `<tr><td colspan="4" style="text-align:center;color:#64748b;padding:14px;">Belum ada file backup tersimpan.</td></tr>`;
    return;
  }

  tbody.innerHTML = data.backups.map(b => `
    <tr>
      <td><code>${b.filename}</code></td>
      <td>${b.size_kb} KB</td>
      <td>${b.created_at}</td>
      <td style="display:flex;gap:6px;justify-content:flex-end;">
        <a class="btn-manage" href="/api/settings/backup/download/${encodeURIComponent(b.filename)}" style="text-decoration:none;">⬇️ Unduh</a>
        <button type="button" class="btn-manage" onclick="restoreLocalBackup('${b.filename}')">♻️ Restore</button>
      </td>
    </tr>
  `).join("");
}

async function createNewBackup() {
  showToast("Membuat arsip backup...");
  const res = await fetch("/api/settings/backup", { method: "POST" });
  const data = await res.json();
  if (res.ok) {
    showToast(`✅ Backup ${data.filename} berhasil dibuat!`);
    loadBackupList();
  }
}

async function restoreLocalBackup(filename) {
  if (!confirm(`Restore data akun & konfigurasi dari '${filename}'?`)) return;
  const res = await fetch("/api/settings/restore-local", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename }),
  });
  if (res.ok) {
    showToast("✅ Restore berhasil diterapkan!");
    loadOverviewAndSystem();
    loadAccountsData();
  }
}

async function uploadAndRestoreBackup(event) {
  event.preventDefault();
  const fileInput = document.getElementById("restore-file-input");
  if (!fileInput || !fileInput.files.length) {
    alert("Pilih file backup (.tar.gz) terlebih dahulu!");
    return;
  }
  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  showToast("Mengunggah & me-restore backup...");
  const res = await fetch("/api/settings/restore-upload", {
    method: "POST",
    body: formData,
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.detail || "Gagal restore backup.");
    return;
  }
  showToast("✅ Backup berhasil di-restore!");
  fileInput.value = "";
  loadBackupList();
  loadAccountsData();
}
