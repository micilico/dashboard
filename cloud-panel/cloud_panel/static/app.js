const CFG = window.__CLOUD_PANEL_CONFIG__ || {};
const PP = String(CFG.publicPrefix || "/cloud-panel").replace(/\/$/, "");
const BASE = `${PP}/`;

const S = {
  csrf: "", path: "", files: [], allFiles: [], page: 1, pageSize: 50,
  sortKey: "name", sortDir: "asc", search: "",
  renameTarget: null, deleteTarget: null, shareTarget: null,
  selected: new Set(), focusedIdx: -1, favs: [],
  view: "files", loading: false, hasMore: true, obs: null,
  diskUsed: "", diskTotal: "", diskPct: 0,
};

const $ = (id) => document.getElementById(id);
const qs = (s, p) => (p || document).querySelector(s);
const qsa = (s, p) => [...(p || document).querySelectorAll(s)];

function rt(p) { return p === "/" ? BASE : `${PP}${p.startsWith("/") ? p : "/" + p}`; }
function au(p) { return rt(`/api${p}`); }

function fmtSize(b) {
  const n = Number(b) || 0; if (n === 0) return "0 o";
  const u = ["o", "Ko", "Mo", "Go", "To"]; const i = Math.min(Math.floor(Math.log(Math.abs(n)) / Math.log(1024)), 4);
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}
function fmtDate(ts) { const n = Number(ts); return n > 0 ? new Date(n * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : ""; }
function fmtRel(t) { if (!t) return "Jamais"; const d = Date.now() - new Date(t).getTime(); if (d < 6e4) return "A l'instant"; if (d < 36e5) return `Il y a ${Math.round(d / 6e4)} min`; return `Il y a ${Math.round(d / 36e5)} h`; }

function fileIcon(name, isDir) {
  if (isDir) return "folder";
  const ext = (name || "").split(".").pop().toLowerCase();
  if (["mp4","mkv","avi","mov","webm","m4v"].includes(ext)) return "video";
  if (["mp3","wav","flac","ogg","m4a","aac"].includes(ext)) return "audio";
  if (["jpg","jpeg","png","gif","webp","svg","bmp","ico"].includes(ext)) return "image";
  if (["pdf"].includes(ext)) return "pdf";
  if (["zip","rar","7z","tar","gz","bz2","xz"].includes(ext)) return "archive";
  if (["doc","docx","xls","xlsx","ppt","pptx","odt","ods"].includes(ext)) return "document";
  return "file";
}
function fileIconSVG(type) {
  const m = {
    folder: '<path d="M3.75 6.75a2 2 0 0 1 2-2H10l2 2.5h6.25a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5.75a2 2 0 0 1-2-2z"/>',
    video: '<circle cx="12" cy="12" r="8.25"/><path d="M10 9.5v5l5-2.5z"/>',
    audio: '<circle cx="12" cy="12" r="8.25"/><path d="M12 8v8M8 10.5v3M16 10.5v3"/>',
    image: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="9" cy="9" r="2"/><path d="M4 16l4-4 3 3 3-4 6 5"/>',
    pdf: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 9h8M8 13h5M8 17h8"/><path d="M15 3v4h4"/>',
    archive: '<path d="M5 8.5h14M5 8.5A2 2 0 0 1 3 6.5v-2A2 2 0 0 1 5 2.5h14a2 2 0 0 1 2 2v2a2 2 0 0 1-2 2M5 8.5v9a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-9"/>',
    document: '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 9h8M8 13h5M8 17h8"/><path d="M15 3v4h4"/>',
    file: '<path d="M7.75 3.75h4.5l6 6v10.5a1 1 0 0 1-1 1H7.75a1 1 0 0 1-1-1V4.75a1 1 0 0 1 1-1z"/><path d="M12.25 3.75v6h6"/>',
  };
  return `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">${m[type] || m.file}</svg>`;
}

// ── API ──
async function api(path, opts = {}, retry = true) {
  const h = new Headers(opts.headers || {});
  h.set("Accept", "application/json");
  const fd = opts.body instanceof FormData;
  if (opts.body && !fd && !h.has("Content-Type")) h.set("Content-Type", "application/json");
  if ((opts.method || "GET").toUpperCase() !== "GET") h.set("X-Cloud-Panel-CSRF", S.csrf);
  const r = await fetchWithRetry(path, { ...opts, headers: h, credentials: "same-origin" });
  const p = await r.json().catch(() => ({}));
  if (r.ok) return p;
  const d = typeof p.detail === "object" && p.detail ? p.detail : {};
  const e = new Error(d.message || p.detail || "Action impossible.");
  e.code = d.code || `http_${r.status}`; e.recovery = d.recovery || "Reessayer"; e.status = r.status;
  if (r.status === 403 && e.code === "csrf_expired" && retry) { await refreshSession(); return api(path, opts, false); }
  throw e;
}
async function refreshSession() {
  try { const r = await api(au("/session"), { cache: "no-store" }, false); S.csrf = r.csrfToken; } catch {}
}

// ── Toast ──
function toast(msg) {
  const el = $("toast"); el.textContent = msg; el.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => el.hidden = true, 4200);
}
function showError(e) { const el = $("alert"); qs("#alertText", el).textContent = e?.message || "Action impossible."; el.hidden = false; }
function clearError() { $("alert").hidden = true; }

// ── Navigation ──
function navigate(p) { S.path = p; S.page = 1; S.files = []; S.hasMore = true; S.selected = new Set(); S.focusedIdx = -1; loadFiles(); updateUrl(); }
function updateUrl() {
  const u = new URL(window.location.href);
  if (S.path) u.searchParams.set("path", S.path); else u.searchParams.delete("path");
  u.searchParams.set("view", S.view);
  window.history.replaceState({}, "", u.toString());
}

// ── Load files ──
async function loadFiles(append) {
  if (S.loading) return; S.loading = true;
  const el = $("scrollSentinel");
  if (!append) { el.classList.remove("loading"); S.files = []; renderFiles(); }
  try {
    const d = await api(au(`/files?path=${encodeURIComponent(S.path)}`));
    S.diskUsed = d.disk_used || ""; S.diskTotal = d.disk_total || ""; S.diskPct = d.disk_percent || 0;
    const all = d.items || [];
    const start = append ? S.files.length : 0;
    const chunk = all.slice(start, start + S.pageSize);
    if (append) S.files = S.files.concat(chunk); else S.files = chunk;
    S.allFiles = all;
    S.hasMore = S.files.length < all.length;
    renderSidebarDisk();
    renderFiles();
    if (S.hasMore) { el.classList.add("loading"); startObserving(); } else { el.classList.remove("loading"); stopObserving(); }
  } catch (e) { showError(e); }
  S.loading = false;
}

// ── Infinite scroll ──
function startObserving() {
  stopObserving();
  S.obs = new IntersectionObserver(([e]) => { if (e.isIntersecting && S.hasMore && !S.loading) loadFiles(true); }, { rootMargin: "200px" });
  S.obs.observe($("scrollSentinel"));
}
function stopObserving() { if (S.obs) { S.obs.disconnect(); S.obs = null; } }

// ── Sort & filter ──
function getSortedFiltered() {
  let items = S.search ? S.allFiles.filter(f => f.name.toLowerCase().includes(S.search.toLowerCase())) : S.allFiles;
  const key = S.sortKey, dir = S.sortDir === "asc" ? 1 : -1;
  items = [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    if (key === "name") return a.name.localeCompare(b.name, "fr") * dir;
    if (key === "size") return ((a.size_bytes || 0) - (b.size_bytes || 0)) * dir;
    if (key === "date") return ((a.modified || 0) - (b.modified || 0)) * dir;
    return 0;
  });
  return items;
}

// ── Render ──
function renderFiles() {
  const all = getSortedFiltered();
  const totalPages = Math.ceil(all.length / S.pageSize);
  const start = (S.page - 1) * S.pageSize;
  const items = all.slice(0, start + S.pageSize);
  const body = $("fileBody");
  const empty = $("emptyState");
  const sentinel = $("scrollSentinel");

  if (items.length === 0) {
    body.replaceChildren(); empty.hidden = false;
    qs("p", empty).textContent = S.search ? "Aucun resultat." : "Ce dossier est vide.";
    sentinel.classList.remove("loading"); return;
  }
  empty.hidden = true;

  body.replaceChildren(...items.map((f, i) => {
    const tr = document.createElement("tr");
    if (S.selected.has(f.path)) tr.classList.add("selected");
    if (i === S.focusedIdx) { tr.classList.add("focused"); tr.tabIndex = 0; }

    // Checkbox
    const td0 = document.createElement("td"); td0.className = "col-check";
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = S.selected.has(f.path);
    cb.addEventListener("change", () => toggleSelect(f.path));
    td0.append(cb);

    // Name
    const td1 = document.createElement("td");
    const nc = document.createElement("div"); nc.className = "file-name-cell";
    const ic = document.createElement("span"); ic.className = `file-icon ${f.is_dir ? "folder" : fileIcon(f.name)}`; ic.innerHTML = fileIconSVG(fileIcon(f.name, f.is_dir));
    const nm = document.createElement("span"); nm.className = `file-name${f.is_dir ? " dir" : ""}`; nm.textContent = f.name; nm.title = f.name;
    if (f.is_dir) nm.addEventListener("click", () => navigate(f.path));
    nc.append(ic, nm); td1.append(nc);

    // Size
    const td2 = document.createElement("td"); td2.className = "size-cell"; td2.textContent = f.is_dir ? "—" : (f.size || fmtSize(f.size_bytes));

    // Date
    const td3 = document.createElement("td"); td3.className = "date-cell"; td3.textContent = f.modified ? fmtDate(f.modified) : "—";

    // Fav
    const td4 = document.createElement("td");
    const fb = document.createElement("button"); fb.className = `fav-btn${S.favs.some(x => x.path === f.path) ? " active" : ""}`;
    fb.setAttribute("aria-label", "Favori"); fb.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor" stroke="currentColor" stroke-width="1"><path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01z"/></svg>';
    fb.addEventListener("click", async (e) => { e.stopPropagation(); await toggleFav(f); });
    td4.append(fb);

    // Actions
    const td5 = document.createElement("td"); td5.className = "action-cell";
    const acts = document.createElement("div"); acts.style.display = "flex"; acts.style.gap = "2px"; acts.style.justifyContent = "flex-end";
    if (!f.is_dir) {
      const dl = document.createElement("a"); dl.className = "action-btn"; dl.href = au(`/files/download?path=${encodeURIComponent(f.path)}`);
      dl.setAttribute("aria-label", "Telecharger"); dl.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 18.25h14"/></svg>';
      acts.append(dl);
    }
    const sh = document.createElement("button"); sh.className = "action-btn"; sh.setAttribute("aria-label", "Partager");
    sh.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M13.5 10.5 10.5 13.5M8.5 15.5l-1.5 1.5a3 3 0 0 0 4.25 4.25l3-3a3 3 0 0 0 0-4.24M15.5 8.5l1.5-1.5a3 3 0 0 0-4.25-4.25l-3 3a3 3 0 0 0 0 4.24"/></svg>';
    sh.addEventListener("click", (e) => { e.stopPropagation(); openShare(f); }); acts.append(sh);
    const rn = document.createElement("button"); rn.className = "action-btn"; rn.setAttribute("aria-label", "Renommer");
    rn.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M15.25 5.25 18.75 8.75M7.75 16.25l-1 2 2-1L16 10 14 8Z"/></svg>';
    rn.addEventListener("click", (e) => { e.stopPropagation(); openRename(f); }); acts.append(rn);
    const dt = document.createElement("button"); dt.className = "action-btn danger"; dt.setAttribute("aria-label", "Supprimer");
    dt.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 7.75h14M9 7.75V5.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2.25M19 7.75v11a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 18.75V7.75"/></svg>';
    dt.addEventListener("click", (e) => { e.stopPropagation(); openDelete(f); }); acts.append(dt);
    td5.append(acts);

    tr.append(td0, td1, td2, td3, td4, td5);
    tr.addEventListener("click", (e) => { if (!e.target.closest("button,a,input")) S.focusedIdx = i; });
    return tr;
  }));

  renderBreadcrumb();
  renderBulkBar();
}

function renderBreadcrumb() {
  const parts = S.path ? S.path.replace(/\\/g, "/").split("/").filter(Boolean) : [];
  const el = $("breadcrumb"); el.replaceChildren();
  const rl = document.createElement("a"); rl.href = "#"; rl.textContent = "Cloud Panel";
  rl.addEventListener("click", (e) => { e.preventDefault(); navigate(""); }); el.append(rl);
  let acc = "";
  for (const p of parts) {
    const sp = document.createElement("span"); sp.className = "sep"; sp.textContent = "/"; el.append(sp);
    acc = acc ? `${acc}/${p}` : p;
    const lk = document.createElement("a"); lk.href = "#"; lk.textContent = p;
    lk.addEventListener("click", (e) => { e.preventDefault(); navigate(acc); }); el.append(lk);
  }
  qs("#sidebarPath").textContent = `/mnt/ultra-media/${S.path ? S.path + "/" : ""}`;
}

function renderSidebarDisk() {
  const el = $("sidebarDisk");
  if (S.diskTotal && S.diskTotal !== "N/A") {
    el.hidden = false;
    const pct = Math.min(100, Math.max(0, S.diskPct));
    qs(".sidebar-disk-fill", el).style.width = `${pct}%`;
    qs(".sidebar-disk-fill", el).style.background = pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warning)" : "var(--success)";
    qs(".sidebar-disk-text", el).textContent = `${S.diskUsed} / ${S.diskTotal}`;
  } else el.hidden = true;
}

function renderBulkBar() {
  const el = $("bulkBar"); const n = S.selected.size;
  if (n === 0) { el.hidden = true; return; }
  el.hidden = false;
  qs("#bulkCount", el).textContent = `${n} selectionne${n > 1 ? "s" : ""}`;
}

function toggleSelect(path) {
  if (S.selected.has(path)) S.selected.delete(path); else S.selected.add(path);
  renderFiles(); renderBulkBar();
}
function selectAll() {
  const items = getSortedFiltered();
  if (S.selected.size === items.filter(f => !f.is_dir).length) S.selected.clear();
  else items.filter(f => !f.is_dir).forEach(f => S.selected.add(f.path));
  renderFiles(); renderBulkBar();
}

// ── Favorites ──
async function loadFavs() {
  try { const d = await api(au("/favorites")); S.favs = d.items || []; renderFavs(); } catch {}
}
async function toggleFav(f) {
  try {
    const exists = S.favs.some(x => x.path === f.path);
    if (exists) await api(au("/favorites/remove"), { method: "POST", body: new URLSearchParams({ path: f.path }) });
    else await api(au("/favorites/add"), { method: "POST", body: new URLSearchParams({ path: f.path, name: f.name, is_dir: f.is_dir ? "true" : "" }) });
    await loadFavs(); renderFiles();
  } catch (e) { toast(e.message); }
}
function renderFavs() {
  const el = $("favoritesList");
  if (!S.favs.length) { el.innerHTML = '<p class="sidebar-empty">Aucun favori</p>'; return; }
  el.replaceChildren(...S.favs.map(f => {
    const b = document.createElement("button"); b.className = "fav-item";
    const s = document.createElement("span"); s.className = "fav-star"; s.textContent = "★";
    const t = document.createElement("span"); t.textContent = f.name; t.title = f.path;
    b.append(s, t);
    b.addEventListener("click", () => navigate(f.path));
    return b;
  }));
}

// ── Share ──
function openShare(f) {
  S.shareTarget = f;
  qs("#shareItem", $("shareDialog")).textContent = f.name;
  qs("#shareResult", $("shareDialog")).hidden = true;
  qs("#shareMessage", $("shareDialog")).textContent = "";
  qs("#shareMode", $("shareDialog")).value = f.is_dir ? "zip" : "file";
  qs("#shareMode", $("shareDialog")).disabled = !f.is_dir;
  qs("#sharePassword", $("shareDialog")).value = "";
  $("shareDialog").showModal();
}
$("confirmShareBtn").addEventListener("click", async () => {
  const f = S.shareTarget; if (!f) return;
  const mode = qs("#shareMode", $("shareDialog")).value;
  const expiry = parseInt(qs("#shareExpiry", $("shareDialog")).value) || 7;
  const password = qs("#sharePassword", $("shareDialog")).value;
  const msg = qs("#shareMessage", $("shareDialog")); msg.textContent = "";
  try {
    const fd = new URLSearchParams({ path: f.path, expiry_days: String(expiry), password });
    let ep = "/share/file";
    if (f.is_dir && mode === "zip") ep = "/share/zip";
    else if (f.is_dir && mode === "folder") ep = "/share/folder";
    const r = await api(au(ep), { method: "POST", body: fd });
    const shareUrl = `${window.location.origin}${PP}/api/download/${r.token}`;
    qs("#shareUrl", $("shareDialog")).value = shareUrl;
    qs("#shareResult", $("shareDialog")).hidden = false;
    msg.textContent = "Lien genere avec succes.";
    generateQR(shareUrl);
  } catch (e) { msg.textContent = e.message; }
});
$("copyLinkBtn").addEventListener("click", () => {
  const inp = qs("#shareUrl", $("shareDialog")); inp.select(); navigator.clipboard?.writeText(inp.value);
  toast("Lien copie.");
});
$("cancelShareBtn").addEventListener("click", () => { $("shareDialog").close(); S.shareTarget = null; });

function generateQR(url) {
  const c = $("qrCanvas"); const sz = 140;
  c.width = sz; c.height = sz; const ctx = c.getContext("2d");
  // Simple QR-like visual indicator when URL is present
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, sz, sz);
  ctx.fillStyle = "#000"; const n = 17, bs = sz / (n + 4), off = bs * 2;
  // Finder patterns
  for (const [ox, oy] of [[0,0],[n-7,0],[0,n-7]]) {
    ctx.fillRect(ox*bs+off, oy*bs+off, 7*bs, 7*bs);
    ctx.fillStyle = "#fff"; ctx.fillRect(ox*bs+off+bs, oy*bs+off+bs, 5*bs, 5*bs);
    ctx.fillStyle = "#000"; ctx.fillRect(ox*bs+off+bs*2, oy*bs+off+bs*2, 3*bs, 3*bs);
    ctx.fillStyle = "#000";
  }
  // Dot pattern based on URL hash
  const h = url.split("").reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 0);
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    if ((x < 7 && y < 7) || (x >= n-7 && y < 7) || (x < 7 && y >= n-7)) continue;
    if ((h >> ((x + y * n) % 31)) & 1) ctx.fillRect(x * bs + off, y * bs + off, bs, bs);
  }
  $("qr-wrap")?.classList.remove("hidden"); // class hidden not used, use attribute
  qs("#shareQR", $("shareDialog")).hidden = false;
}

// ── Dialogs ──
function openRename(f) { S.renameTarget = f; $("renameInput").value = f.name; qs("#renameMessage", $("renameDialog")).textContent = ""; $("renameDialog").showModal(); }
function openDelete(f) { S.deleteTarget = f; qs("#deleteName", $("deleteDialog")).textContent = f.name; $("deleteDialog").showModal(); }

// ── Events ──
$("btnUpload").addEventListener("click", () => $("uploadDialog").showModal());
$("btnMkdir").addEventListener("click", () => { $("mkdirNameInput").value = ""; qs("#mkdirMessage", $("mkdirDialog")).textContent = ""; $("mkdirDialog").showModal(); });
$("btnSync").addEventListener("click", async () => {
  try { await api(au("/files/refresh"), { method: "POST" }); toast("Cache vide."); loadFiles(); } catch (e) { showError(e); }
});

// Sidebar nav
qsa("[data-nav]").forEach(b => b.addEventListener("click", () => {
  const v = b.dataset.nav;
  if (v === "root") navigate("");
  else if (v === "parent") { const p = S.path.split("/").filter(Boolean).slice(0, -1).join("/"); navigate(p); }
  else if (v === "history") switchView("history");
  else if (v === "links") switchView("links");
  else if (v === "stats") switchView("stats");
}));

function switchView(v) {
  S.view = v; updateUrl();
  ["files","history","links","stats"].forEach(x => {
    const el = $(x + "View"); if (el) el.hidden = x !== v;
  });
  qsa(".sidebar-link").forEach(b => b.classList.toggle("active", b.dataset.nav === v));
  if (v === "history") loadHistory();
  if (v === "links") loadLinks();
  if (v === "stats") loadStats();
  if (v === "files") { stopObserving(); loadFiles(); }
}

// ── Keyboard ──
document.addEventListener("keydown", (e) => {
  if ($("uploadDialog").open || $("mkdirDialog").open || $("renameDialog").open || $("deleteDialog").open || $("shareDialog").open || $("previewDialog").open) {
    if (e.key === "Escape") { e.preventDefault(); document.querySelector("dialog[open]")?.close(); }
    return;
  }
  const items = getSortedFiltered();
  if (e.key === "ArrowDown" && S.focusedIdx < items.length - 1) { S.focusedIdx++; e.preventDefault(); scrollToRow(); renderFiles(); }
  if (e.key === "ArrowUp" && S.focusedIdx > 0) { S.focusedIdx--; e.preventDefault(); scrollToRow(); renderFiles(); }
  if (e.key === "Enter" && S.focusedIdx >= 0) {
    const f = items[S.focusedIdx]; if (f.is_dir) navigate(f.path);
    else window.location.href = au(`/files/download?path=${encodeURIComponent(f.path)}`);
  }
  if (e.key === "Delete" && S.focusedIdx >= 0) openDelete(items[S.focusedIdx]);
  if (e.key === "F2" && S.focusedIdx >= 0) { e.preventDefault(); openRename(items[S.focusedIdx]); }
  if (e.key === "a" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); selectAll(); }
});
function scrollToRow() {
  const row = qs(`#fileBody tr:nth-child(${S.focusedIdx + 1})`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

// Form submit prevention
qsa("dialog form[method=dialog]").forEach(f => f.addEventListener("submit", e => e.preventDefault()));

// ── Confirm buttons ──
$("confirmMkdirBtn").addEventListener("click", async () => {
  const name = $("mkdirNameInput").value.trim(); if (!name) { qs("#mkdirMessage", $("mkdirDialog")).textContent = "Nom requis."; return; }
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("name", name);
    await api(au("/files/mkdir"), { method: "POST", body: fd }); toast(`Dossier "${name}" cree.`); $("mkdirDialog").close(); loadFiles();
  } catch (e) { qs("#mkdirMessage", $("mkdirDialog")).textContent = e.message; }
});
$("cancelMkdirBtn").addEventListener("click", () => $("mkdirDialog").close());

$("confirmRenameBtn").addEventListener("click", async () => {
  const n = $("renameInput").value.trim(); if (!n || !S.renameTarget) return;
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("old_name", S.renameTarget.name); fd.append("new_name", n);
    await api(au("/files/rename"), { method: "POST", body: fd }); toast("Renomme."); $("renameDialog").close(); S.renameTarget = null; loadFiles();
  } catch (e) { qs("#renameMessage", $("renameDialog")).textContent = e.message; }
});
$("cancelRenameBtn").addEventListener("click", () => { $("renameDialog").close(); S.renameTarget = null; });

$("confirmDeleteBtn").addEventListener("click", async () => {
  if (!S.deleteTarget) return;
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("name", S.deleteTarget.name);
    await api(au("/files/delete"), { method: "POST", body: fd }); toast("Supprime."); $("deleteDialog").close(); S.deleteTarget = null; loadFiles();
  } catch (e) { showError(e); }
});
$("cancelDeleteBtn").addEventListener("click", () => { $("deleteDialog").close(); S.deleteTarget = null; });

// ── Upload ──
$("uploadZone").addEventListener("click", () => $("fileInput").click());
$("uploadZone").addEventListener("dragover", e => { e.preventDefault(); $("uploadZone").classList.add("dragover"); });
$("uploadZone").addEventListener("dragleave", () => $("uploadZone").classList.remove("dragover"));
$("uploadZone").addEventListener("drop", e => { e.preventDefault(); $("uploadZone").classList.remove("dragover"); if (e.dataTransfer.files.length) startUpload(e.dataTransfer.files); });
$("fileInput").addEventListener("change", () => { if ($("fileInput").files.length) { startUpload($("fileInput").files); $("fileInput").value = ""; } });
$("cancelUploadBtn").addEventListener("click", () => { $("uploadDialog").close(); $("uploadProgress").hidden = true; });

async function startUpload(files) {
  $("uploadMessage").textContent = ""; $("uploadProgress").hidden = false;
  $("uploadProgress").replaceChildren(...Array.from(files).map(f => {
    const r = document.createElement("div"); r.className = "progress-row"; r.dataset.fn = f.name;
    r.innerHTML = `<span class="pname">${f.name}</span><div class="ptrack"><div class="pfill" style="width:0"></div></div><span class="pstatus">0%</span>`;
    return r;
  }));
  for (const f of files) await uploadOne(f);
  toast("Upload termine."); $("uploadProgress").hidden = true; loadFiles();
}

async function uploadOne(file) {
  const fd = new FormData(); fd.append("path", S.path); fd.append("file", file);
  const row = qs(`[data-fn="${file.name}"]`, $("uploadProgress"));
  const fill = qs(".pfill", row); const st = qs(".pstatus", row);
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", au("/files/upload")); xhr.setRequestHeader("X-Cloud-Panel-CSRF", S.csrf);
    xhr.upload.addEventListener("progress", e => { if (e.lengthComputable && fill && st) { const p = Math.round(e.loaded / e.total * 100); fill.style.width = p + "%"; st.textContent = p + "%"; } });
    await new Promise((res, rej) => { xhr.onload = () => xhr.status < 300 ? res() : rej(new Error("Upload echoue")); xhr.onerror = () => rej(new Error("Erreur")); xhr.send(fd); });
  } catch (e) { if (st) st.textContent = "Erreur"; showError(e); }
}

// ── Select All ──
$("selectAll").addEventListener("change", selectAll);

// ── Sort ──
qsa(".sortable").forEach(th => th.addEventListener("click", () => {
  const key = th.dataset.sort;
  if (S.sortKey === key) S.sortDir = S.sortDir === "asc" ? "desc" : "asc";
  else { S.sortKey = key; S.sortDir = key === "name" ? "asc" : "desc"; }
  S.page = 1;
  renderSortHeaders(); renderFiles();
}));
function renderSortHeaders() {
  qsa(".sortable").forEach(th => {
    th.classList.toggle("asc", th.dataset.sort === S.sortKey && S.sortDir === "asc");
    th.classList.toggle("desc", th.dataset.sort === S.sortKey && S.sortDir === "desc");
  });
}

// ── Search ──
$("searchInput").addEventListener("input", () => { S.search = $("searchInput").value.trim(); S.page = 1; renderFiles(); });

// ── Bulk actions ──
$("bulkClear").addEventListener("click", () => { S.selected.clear(); renderFiles(); renderBulkBar(); });
$("bulkDelete").addEventListener("click", async () => {
  if (!S.selected.size) return;
  if (!confirm(`Supprimer ${S.selected.size} element(s) ?`)) return;
  const it = [...S.selected]; const ps = $("progressOverlay"); ps.hidden = false; qs("#progressTitle", ps).textContent = "Suppression...";
  let done = 0;
  for (const p of it) {
    try { const fd = new FormData(); const pp = p.split("/").slice(0, -1).join("/"); const nm = p.split("/").pop(); fd.append("path", pp); fd.append("name", nm); await api(au("/files/delete"), { method: "POST", body: fd }); } catch {}
    done++; qs("#progressBarFill", ps).style.width = `${done / it.length * 100}%`; qs("#progressStatus", ps).textContent = `${done}/${it.length}`;
  }
  ps.hidden = true; S.selected.clear(); toast("Suppression terminee."); loadFiles();
});
$("bulkShare").addEventListener("click", async () => {
  if (!S.selected.size) return;
  const it = [...S.selected]; const ps = $("progressOverlay"); ps.hidden = false; qs("#progressTitle", ps).textContent = "Generation de liens...";
  let results = [];
  for (const p of it) {
    try { const r = await api(au("/share/file"), { method: "POST", body: new URLSearchParams({ path: p, expiry_days: "7", password: "" }) }); results.push(r); } catch {}
  }
  ps.hidden = true;
  const msg = results.map(r => `${window.location.origin}${PP}/api/download/${r.token}`).join("\n");
  navigator.clipboard?.writeText(msg); toast(`${results.length} lien(s) generes et copies.`);
});
$("bulkDownload").addEventListener("click", () => {
  const it = [...S.selected]; if (!it.length) return;
  // Download first selected file (single file download for now)
  window.location.href = au(`/files/download?path=${encodeURIComponent(it[0])}`);
});

// ── History ──
async function loadHistory() {
  try { const d = await api(au("/history/data")); const items = d.items || [];
    const b = $("historyBody"); if (!items.length) { b.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--muted)">Aucun historique.</td></tr>'; return; }
    b.replaceChildren(...items.map(h => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td class="date-cell">${fmtDate(h.date)}</td><td>${h.filename}</td><td class="size-cell">${fmtSize(h.size_bytes)}</td><td>${h.action}</td><td>${h.token ? `<a href="${au(`/download/${h.token}`)}" target="_blank">Lien</a>` : "—"}</td>`;
      return tr;
    }));
  } catch (e) { showError(e); }
}

// ── Links ──
async function loadLinks() {
  try { const d = await api(au("/links")); const items = d.items || [];
    const b = $("linksBody"); if (!items.length) { b.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted)">Aucun lien.</td></tr>'; return; }
    b.replaceChildren(...items.map(l => {
      const tr = document.createElement("tr");
      const expired = l.expires_at && l.expires_at < Date.now() / 1000;
      const status = l.is_revoked ? "Revogue" : expired ? "Expire" : "Actif";
      const sc = l.is_revoked ? "var(--danger)" : expired ? "var(--warning)" : "var(--success)";
      tr.innerHTML = `<td>${l.filename}</td><td style="font-size:.8rem;font-family:monospace">${l.token.slice(0, 16)}...</td><td>${l.download_count}</td><td class="date-cell">${l.expires_at ? fmtDate(l.expires_at) : "Jamais"}</td><td style="color:${sc};font-weight:600">${status}</td><td class="action-cell force">`;
      const acts = qs("td:last-child", tr);
      if (!l.is_revoked && !expired) {
        const rv = document.createElement("button"); rv.className = "action-btn danger"; rv.setAttribute("aria-label", "Revoguer");
        rv.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 7.75h14M9 7.75V5.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2.25"/></svg>';
        rv.addEventListener("click", async () => { await api(au("/links/revoke"), { method: "POST", body: new URLSearchParams({ token: l.token }) }); toast("Lien revoque."); loadLinks(); });
        acts.append(rv);
        const ex = document.createElement("button"); ex.className = "action-btn"; ex.setAttribute("aria-label", "Prolonger");
        ex.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"/></svg>';
        ex.addEventListener("click", async () => { await api(au("/links/extend"), { method: "POST", body: new URLSearchParams({ token: l.token, days: "7" }) }); toast("Lien prolonge de 7 jours."); loadLinks(); });
        acts.append(ex);
      }
      const cp = document.createElement("button"); cp.className = "action-btn"; cp.setAttribute("aria-label", "Copier lien");
      cp.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      cp.addEventListener("click", () => { navigator.clipboard?.writeText(`${window.location.origin}${PP}/api/download/${l.token}`); toast("Lien copie."); });
      acts.append(cp);
      return tr;
    }));
  } catch (e) { showError(e); }
}

// ── Stats ──
async function loadStats() {
  try { const d = await api(au("/stats")); const g = $("statsGrid");
    const cards = [
      ["total_links", "Liens crees"], ["active_links", "Liens actifs"], ["expired_links", "Liens expires"],
      ["revoked_links", "Liens revoques"], ["total_downloads", "Telechargements"], ["total_history", "Uploads"], ["total_favorites", "Favoris"],
    ];
    g.replaceChildren(...cards.map(([k, l]) => {
      const c = document.createElement("div"); c.className = "stat-card";
      c.innerHTML = `<div class="stat-value">${d[k] ?? "—"}</div><div class="stat-label">${l}</div>`;
      return c;
    }));
  } catch (e) { showError(e); }
}

// ── Retry ──
$("retryButton").addEventListener("click", () => { clearError(); loadFiles(); });

// ── Init ──
async function init() {
  const u = new URL(window.location.href);
  const v = u.searchParams.get("view") || "files";
  S.path = u.searchParams.get("path") || "";
  await refreshSession();
  await loadFavs();
  switchView(v);
}
init();
