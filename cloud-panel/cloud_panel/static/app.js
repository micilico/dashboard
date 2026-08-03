const CFG = window.__CLOUD_PANEL_CONFIG__ || {};
const PP = String(CFG.publicPrefix || "/cloud-panel").replace(/\/$/, "");
const BASE = `${PP}/`;
window.DashboardNavigation?.configure(CFG, "cloud");

const S = {
  csrf: "", path: "", files: [], allFiles: [], page: 1, pageSize: 50,
  sortKey: "name", sortDir: "asc", search: "",
  renameTarget: null, deleteTarget: null, shareTarget: null,
  selected: new Set(), selectedItems: new Map(), focusedIdx: -1, favs: [],
  selectionMode: false, actionMenuTarget: null, actionMenuInvoker: null,
  view: "files", loading: false, hasMore: true, obs: null,
  diskUsed: "", diskTotal: "", diskPct: 0, totalItems: 0,
  uploadRows: new Map(),
};

const $ = (id) => document.getElementById(id);
const qs = (s, p) => (p || document).querySelector(s);
const qsa = (s, p) => [...(p || document).querySelectorAll(s)];

const inspectorToggle = $("inspectorToggle");
const cloudInspector = $("cloudInspector");
if (cloudInspector && window.matchMedia("(max-width: 767px)").matches) cloudInspector.hidden = true;
inspectorToggle?.addEventListener("click", () => {
  const open = cloudInspector.hidden;
  cloudInspector.hidden = !open;
  inspectorToggle.setAttribute("aria-expanded", open ? "true" : "false");
  inspectorToggle.textContent = open ? "Masquer les informations du cloud" : "Afficher les informations du cloud";
});

function rt(p) { return p === "/" ? BASE : `${PP}${p.startsWith("/") ? p : "/" + p}`; }
function au(p) { return rt(`/api${p}`); }
function shareUrl(token) { return `${window.location.origin}${rt(`/download/${token}`)}`; }

const fmtSize = formatBytes;
function fmtDate(ts) { const n = Number(ts); return n > 0 ? new Date(n * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : ""; }
function fmtRel(t) { if (!t) return "Jamais"; const d = Date.now() - new Date(t).getTime(); if (d < 6e4) return "À l’instant"; if (d < 36e5) return `Il y a ${Math.round(d / 6e4)} min`; return `Il y a ${Math.round(d / 36e5)} h`; }

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
const cloudApiClient = createApiClient({
  csrfHeader: "X-Cloud-Panel-CSRF",
  getCsrfToken: () => S.csrf,
  setCsrfToken: (token) => { S.csrf = token; },
  sessionPath: au("/session"),
  fetchOptions: { timeout: 60000 },
  refreshAttempts: 3,
});
const api = cloudApiClient.request;
const refreshSession = async () => {
  try { await cloudApiClient.refreshSession(); }
  catch { toast("Erreur: impossible d'initialiser la session. Rechargez la page."); }
};

// ── Toast ──
const toast = createToast(() => $("toast"));
function setButtonBusy(button, busy) {
  if (!button) return;
  button.disabled = busy;
  button.classList.toggle("is-loading", busy);
  button.setAttribute("aria-busy", busy ? "true" : "false");
}
function showError(e) { showErrorMessage($("alert"), e, { messageElement: qs("#alertText", $("alert")), fallback: "Action impossible." }); }
function clearError() { $("alert").hidden = true; }

// ── Navigation ──
function navigate(p) {
  S.view = "files"; S.path = p; S.page = 1; S.files = []; S.hasMore = true; S.selected = new Set(); S.selectedItems = new Map(); S.focusedIdx = -1; S.selectionMode = false;
  ["files","history","links","stats"].forEach(x => { const el = $(x + "View"); if (el) el.hidden = x !== "files"; });
  qsa(".sidebar-link").forEach(b => b.classList.toggle("active", b.dataset.nav === "files"));
  loadFiles(); updateUrl();
}
function updateUrl() {
  const u = new URL(window.location.href);
  if (S.path) u.searchParams.set("path", S.path); else u.searchParams.delete("path");
  u.searchParams.set("view", S.view);
  if (S.search) u.searchParams.set("search", S.search); else u.searchParams.delete("search");
  if (S.sortKey !== "name") u.searchParams.set("sort", S.sortKey); else u.searchParams.delete("sort");
  if (S.sortDir !== "asc") u.searchParams.set("direction", S.sortDir); else u.searchParams.delete("direction");
  if (S.page > 1) u.searchParams.set("page", String(S.page)); else u.searchParams.delete("page");
  window.history.replaceState({}, "", u.toString());
}

// ── Load files ──
async function loadFiles(append) {
  if (S.loading) return; S.loading = true;
  const el = $("scrollSentinel");
  if (!append) { el.classList.remove("loading"); S.files = []; renderFileSkeleton(); }
  try {
    const params = new URLSearchParams({ path: S.path, offset: String((S.page - 1) * S.pageSize), limit: String(S.pageSize) });
    if (S.search) params.set("search", S.search);
    const d = await api(au(`/files?${params.toString()}`));
    S.diskUsed = d.disk_used || ""; S.diskTotal = d.disk_total || ""; S.diskPct = d.disk_percent || 0;
    const items = d.items || [];
    S.files = items;
    S.allFiles = items;
    S.totalItems = Number(d.total) || items.length;
    S.hasMore = false;
    renderSidebarDisk();
    renderFiles();
    if (S.hasMore) { el.classList.add("loading"); startObserving(); } else { el.classList.remove("loading"); stopObserving(); }
  } catch (e) { $("fileBody").replaceChildren(); showError(e); }
  S.loading = false;
}

function renderFileSkeleton() {
  $("emptyState").hidden = true;
  $("paginationBar").hidden = true;
  $("fileBody").replaceChildren(...Array.from({ length: 5 }, () => {
    const row = document.createElement("tr");
    row.className = "skeleton-row";
    const cell = document.createElement("td");
    cell.colSpan = 6;
    const line = document.createElement("span");
    line.className = "skeleton-line";
    cell.append(line);
    row.append(cell);
    return row;
  }));
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
function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(S.totalItems / S.pageSize));
  S.page = Math.max(1, Math.min(totalPages, S.page + delta));
  loadFiles(false); updateUrl();
}

function renderFiles() {
  const all = getSortedFiltered();
  const totalPages = Math.max(1, Math.ceil(S.totalItems / S.pageSize));
  S.page = Math.min(S.page, totalPages);
  const items = all;
  const body = $("fileBody");
  const empty = $("emptyState");
  const sentinel = $("scrollSentinel");
  const pagBar = $("paginationBar");
  $("filesView").classList.toggle("selection-mode", S.selectionMode);
  const selectionButton = $("btnSelectionMode");
  if (selectionButton) {
    selectionButton.setAttribute("aria-pressed", S.selectionMode ? "true" : "false");
    selectionButton.classList.toggle("active", S.selectionMode);
    const label = qs("span", selectionButton);
    if (label) label.textContent = S.selectionMode ? "Terminer" : "Sélectionner";
  }
  closeFileActionMenu();

  if (items.length === 0) {
    body.replaceChildren(); empty.hidden = false;
    qs("p", empty).textContent = S.search ? "Aucun resultat." : "Ce dossier est vide.";
    sentinel.classList.remove("loading"); pagBar.hidden = true; return;
  }
  empty.hidden = true;

  body.replaceChildren(...items.map((f, i) => {
    const tr = document.createElement("tr");
    tr.dataset.path = f.path;
    if (S.selected.has(f.path)) tr.classList.add("selected");
    if (i === S.focusedIdx) { tr.classList.add("focused"); tr.tabIndex = 0; }

    // Checkbox
    const td0 = document.createElement("td"); td0.className = "col-check";
    const cb = document.createElement("input"); cb.type = "checkbox";
    cb.checked = S.selected.has(f.path);
    cb.setAttribute("aria-label", `Sélectionner ${f.name}`);
    cb.addEventListener("change", () => toggleSelect(f));
    td0.append(cb);

    // Name
    const td1 = document.createElement("td");
    const nc = document.createElement("div"); nc.className = "file-name-cell";
    const ic = document.createElement("span"); ic.className = `file-icon ${f.is_dir ? "folder" : fileIcon(f.name)}`; ic.innerHTML = fileIconSVG(fileIcon(f.name, f.is_dir));
    const nm = document.createElement(f.is_dir ? "button" : "span");
    nm.className = `file-name${f.is_dir ? " dir" : ""}`;
    nm.textContent = f.name;
    nm.title = f.name;
    if (f.is_dir) {
      nm.type = "button";
      nm.setAttribute("aria-label", `Ouvrir ${f.name}`);
      nm.addEventListener("click", () => {
        if (S.selectionMode) toggleSelect(f);
        else navigate(f.path);
      });
    }
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
    const acts = document.createElement("div"); acts.className = "row-actions";
    const more = document.createElement("button"); more.className = "action-btn"; more.type = "button";
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.setAttribute("aria-label", `Actions pour ${f.name}`);
    more.innerHTML = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12h.01M12 12h.01M19 12h.01"/></svg>';
    more.addEventListener("click", (e) => { e.stopPropagation(); openFileActionMenu(f, e.currentTarget); });
    acts.append(more);
    td5.append(acts);

    tr.append(td0, td1, td2, td3, td4, td5);
    tr.draggable = true;
    tr.addEventListener("dragstart", (e) => dragPayload(e, f));
    tr.addEventListener("dragend", clearDrag);
    if (f.is_dir) {
      tr.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        tr.classList.add("drop-target");
      });
      tr.addEventListener("dragleave", () => tr.classList.remove("drop-target"));
      tr.addEventListener("drop", (e) => {
        e.preventDefault();
        tr.classList.remove("drop-target");
        const items = readDragItems(e);
        if (items.length) moveItemsTo(f.path, items);
        else if (e.dataTransfer.files && e.dataTransfer.files.length) startUpload(e.dataTransfer.files, f.path);
      });
    }
    tr.addEventListener("click", (e) => {
      if (e.target.closest("button,a,input")) return;
      S.focusedIdx = i;
      if (S.selectionMode) toggleSelect(f);
    });
    return tr;
  }));

  renderBreadcrumb();
  renderBulkBar();

  // Pagination
  if (S.totalItems > S.pageSize) {
    pagBar.hidden = false;
    $("pageInfo").textContent = `Page ${S.page} / ${totalPages} · ${S.totalItems} élément${S.totalItems > 1 ? "s" : ""}`;
    $("prevPageBtn").disabled = S.page <= 1;
    $("nextPageBtn").disabled = S.page >= totalPages;
  } else {
    pagBar.hidden = true;
  }
}

function renderBreadcrumb() {
  const parts = S.path ? S.path.replace(/\\/g, "/").split("/").filter(Boolean) : [];
  const el = $("breadcrumb"); el.replaceChildren();
  const rl = document.createElement("a"); rl.href = "#"; rl.textContent = "Cloud";
  rl.addEventListener("click", (e) => { e.preventDefault(); navigate(""); });
  makeDropTarget(rl, "");
  el.append(rl);
  let acc = "";
  for (const p of parts) {
    const sp = document.createElement("span"); sp.className = "sep"; sp.textContent = "/"; el.append(sp);
    acc = acc ? `${acc}/${p}` : p;
    const lk = document.createElement("a"); lk.href = "#"; lk.textContent = p;
    lk.addEventListener("click", (e) => { e.preventDefault(); navigate(acc); });
    makeDropTarget(lk, acc);
    el.append(lk);
  }
  qs("#sidebarPath").textContent = `/mnt/ultra-media/${S.path ? S.path + "/" : ""}`;
}

// ── Drag & drop (déplacer) ──
let dragItems = [];

function dragPayload(e, f) {
  const selectedPaths = [...S.selected];
  const items = selectedPaths.includes(f.path) && selectedPaths.length
    ? selectedPaths.map(p => S.selectedItems.get(p) || { path: p, name: p.split("/").pop(), is_dir: false })
    : [f];
  dragItems = items.map(i => ({ path: i.path, name: i.name, is_dir: Boolean(i.is_dir) }));
  e.dataTransfer.setData("application/x-cloud-item", JSON.stringify(dragItems));
  e.dataTransfer.setData("text/plain", dragItems.map(i => i.name).join(", "));
  e.dataTransfer.effectAllowed = "move";
  qsa("#fileBody tr").forEach(tr => {
    if (dragItems.some(i => i.path === tr.dataset.path)) tr.classList.add("dragging");
  });
}

function clearDrag() {
  qsa("#fileBody tr.dragging").forEach(tr => tr.classList.remove("dragging"));
  dragItems = [];
}

function readDragItems(e) {
  const raw = e.dataTransfer.getData("application/x-cloud-item");
  if (!raw) return [];
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function dropLabel(destPath) {
  return destPath ? destPath.split("/").filter(Boolean).pop() : "Racine";
}

async function moveItemsTo(destPath, items) {
  const label = dropLabel(destPath);
  const names = items.map(i => i.name);
  const srcDirs = new Set(items.map(i => i.path.split("/").slice(0, -1).join("/")));
  if (srcDirs.size === 1 && [...srcDirs][0] === destPath) {
    toast(`« ${names.join(", ")} » est déjà dans « ${label} »`);
    return;
  }
  if (items.some(it => it.is_dir && (destPath === it.path || destPath.startsWith(it.path + "/")))) {
    toast("Impossible de déplacer un dossier dans lui-même");
    return;
  }
  let moved = 0;
  const errors = [];
  for (const it of items) {
    const srcDir = it.path.split("/").slice(0, -1).join("/");
    const name = it.path.split("/").pop();
    const fd = new FormData();
    fd.append("path", srcDir);
    fd.append("name", name);
    fd.append("dest", destPath);
    try {
      await api(au("/files/move"), { method: "POST", body: fd });
      moved++;
    } catch (e) {
      errors.push(`${name} : ${e.message}`);
    }
  }
  if (moved) {
    toast(moved === items.length
      ? (items.length === 1 ? `« ${items[0].name} » déplacé vers « ${label} »` : `${moved} éléments déplacés vers « ${label} »`)
      : `${moved}/${items.length} éléments déplacés`);
  }
  if (errors.length) {
    toast(`Déplacement refusé : ${errors.slice(0, 2).join(" · ")}`);
  }
  S.selected.clear();
  S.selectedItems.clear();
  setSelectionMode(false);
  loadFiles();
}

function makeDropTarget(el, destPathOrGetter) {
  if (!el || el.dataset.dropTarget) return;
  el.dataset.dropTarget = "1";
  el.classList.add("drop-crumb");
  const resolveDest = () => (typeof destPathOrGetter === "function" ? destPathOrGetter() : destPathOrGetter);
  el.addEventListener("dragover", (e) => {
    if (!dragItems.length) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add("drop-active");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop-active"));
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    el.classList.remove("drop-active");
    const items = readDragItems(e);
    if (items.length) moveItemsTo(resolveDest(), items);
  });
}

function renderSidebarDisk() {
  const el = $("sidebarDisk");
  if (S.diskTotal && S.diskTotal !== "N/A") {
    el.hidden = false;
    const pct = Math.min(100, Math.max(0, S.diskPct));
    qs(".sidebar-disk-fill", el).style.width = `${pct}%`;
    qs(".sidebar-disk-fill", el).style.background = pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warning)" : "var(--success)";
    qs(".sidebar-disk-text", el).textContent = `${S.diskUsed} / ${S.diskTotal}`;
    window.DashboardDOM?.updateDiskRing({ percent: pct, usedLabel: S.diskUsed, totalLabel: S.diskTotal });
  } else {
    el.hidden = true;
    window.DashboardDOM?.hideDiskRing();
  }
}

function renderBulkBar() {
  const el = $("bulkBar"); const n = S.selected.size;
  if (n === 0) { el.hidden = true; return; }
  S.selectionMode = true;
  el.hidden = false;
  qs("#bulkCount", el).textContent = `${n} sélectionné${n > 1 ? "s" : ""}`;
  const selectedFolder = getSingleSelectedFolder();
  $("bulkShareFolder").hidden = !selectedFolder;
}

function getSingleSelectedFolder() {
  if (S.selected.size !== 1) return null;
  const selectedPath = [...S.selected][0];
  const selectedItem = S.selectedItems.get(selectedPath);
  if (selectedItem?.is_dir) return selectedItem;
  return [...S.files, ...S.allFiles].find(item => item.path === selectedPath && item.is_dir) || null;
}

function toggleSelect(itemOrPath) {
  S.selectionMode = true;
  const item = typeof itemOrPath === "string" ? null : itemOrPath;
  const path = item?.path || itemOrPath;
  if (S.selected.has(path)) S.selected.delete(path); else S.selected.add(path);
  if (S.selected.has(path) && item) S.selectedItems.set(path, item);
  else S.selectedItems.delete(path);
  renderFiles(); renderBulkBar();
}
function selectAll() {
  S.selectionMode = true;
  const items = getSortedFiltered();
  const files = items.filter(f => !f.is_dir);
  if (S.selected.size === files.length) {
    S.selected.clear();
    S.selectedItems.clear();
  } else {
    files.forEach(f => {
      S.selected.add(f.path);
      S.selectedItems.set(f.path, f);
    });
  }
  renderFiles(); renderBulkBar();
}

function setSelectionMode(enabled) {
  S.selectionMode = enabled;
  if (!enabled) {
    S.selected.clear();
    S.selectedItems.clear();
  }
  renderFiles();
  renderBulkBar();
}

function menuItem(label, icon, action, options = {}) {
  const item = document.createElement(options.href ? "a" : "button");
  if (options.danger) item.classList.add("danger");
  item.setAttribute("role", "menuitem");
  if (options.href) item.href = options.href;
  else item.type = "button";
  item.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${icon}</svg><span>${label}</span>`;
  item.addEventListener("click", (event) => {
    if (!options.href) event.preventDefault();
    closeFileActionMenu();
    action?.(event);
  });
  return item;
}

function closeFileActionMenu() {
  const menu = $("fileActionMenu");
  if (!menu) return;
  menu.hidden = true;
  menu.replaceChildren();
  if (S.actionMenuInvoker) S.actionMenuInvoker.setAttribute("aria-expanded", "false");
  S.actionMenuTarget = null;
  S.actionMenuInvoker = null;
}

function openFileActionMenu(file, invoker) {
  const menu = $("fileActionMenu");
  if (!menu) return;
  if (S.actionMenuTarget?.path === file.path) { closeFileActionMenu(); return; }
  closeFileActionMenu();
  S.actionMenuTarget = file;
  S.actionMenuInvoker = invoker;
  invoker.setAttribute("aria-expanded", "true");
  const items = [];
  if (!file.is_dir) {
    items.push(menuItem("Télécharger", '<path d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 18.25h14"/>', null, { href: au(`/files/download?path=${encodeURIComponent(file.path)}`) }));
    items.push(menuItem("Aperçu", '<circle cx="12" cy="12" r="8.25"/><path d="M12 8v4l3 3"/>', () => openPreview(file, invoker)));
  }
  items.push(menuItem("Partager", '<path d="M13.5 10.5 10.5 13.5M8.5 15.5l-1.5 1.5a3 3 0 0 0 4.25 4.25l3-3a3 3 0 0 0 0-4.24M15.5 8.5l1.5-1.5a3 3 0 0 0-4.25-4.25l-3 3a3 3 0 0 0 0 4.24"/>', () => openShare(file, invoker)));
  items.push(menuItem("Renommer", '<path d="M15.25 5.25 18.75 8.75M7.75 16.25l-1 2 2-1L16 10 14 8Z"/>', () => openRename(file, invoker)));
  items.push(menuItem("Sélectionner", '<path d="M9 11.5 11 13.5 15.5 8.5"/><rect x="4" y="4" width="16" height="16" rx="3"/>', () => toggleSelect(file)));
  if (file.is_dir) {
    items.push(menuItem("Dossier navigable", '<path d="M3.75 6.75a2 2 0 0 1 2-2H10l2 2.5h6.25a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5.75a2 2 0 0 1-2-2z"/><path d="M13.5 10.5 10.5 13.5M8.5 15.5l-1.5 1.5a3 3 0 0 0 4.25 4.25l3-3a3 3 0 0 0 0-4.24"/>', () => openShare(file, invoker)));
  }
  items.push(menuItem("Supprimer", '<path d="M5 7.75h14M9 7.75V5.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2.25M19 7.75v11a1.5 1.5 0 0 1-1.5 1.5H6.5A1.5 1.5 0 0 1 5 18.75V7.75"/>', () => openDelete(file, invoker), { danger: true }));
  menu.replaceChildren(...items);
  const rect = invoker.getBoundingClientRect();
  menu.hidden = false;
  const width = Math.min(240, window.innerWidth - 24);
  menu.style.width = `${width}px`;
  menu.style.left = `${Math.max(12, Math.min(window.innerWidth - width - 12, rect.right - width))}px`;
  menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 12, rect.bottom + 8)}px`;
  qs("[role='menuitem']", menu)?.focus();
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
function openShare(f, trigger) {
  S.shareTarget = f;
  qs("#shareItem", $("shareDialog")).textContent = f.name;
  qs("#shareResult", $("shareDialog")).hidden = true;
  qs("#shareMessage", $("shareDialog")).textContent = "";
  const modeSelect = qs("#shareMode", $("shareDialog"));
  qsa("option", modeSelect).forEach(option => {
    option.disabled = f.is_dir ? option.value === "file" : option.value !== "file";
  });
  modeSelect.value = f.is_dir ? "folder" : "file";
  modeSelect.disabled = !f.is_dir;
  qs("#sharePassword", $("shareDialog")).value = "";
  openDialog($("shareDialog"), trigger);
}
function openShareCurrentFolder(trigger) {
  const cleanPath = (S.path || "").replace(/\/+$/, "");
  const folderName = cleanPath ? cleanPath.split("/").filter(Boolean).pop() : "Racine cloud";
  openShare({ name: folderName, path: cleanPath, is_dir: true }, trigger);
}
$("confirmShareBtn").addEventListener("click", async () => {
  const f = S.shareTarget; if (!f) return;
  const mode = qs("#shareMode", $("shareDialog")).value;
  const expiry = parseInt(qs("#shareExpiry", $("shareDialog")).value) || 7;
  const password = qs("#sharePassword", $("shareDialog")).value;
  const msg = qs("#shareMessage", $("shareDialog")); msg.textContent = "";
  setButtonBusy($("confirmShareBtn"), true);
  try {
    const fd = new URLSearchParams({ path: f.path, expiry_days: String(expiry), password });
    let ep = "/share/file";
    if (f.is_dir && mode === "folder") ep = "/share/folder";
    msg.textContent = "Génération en cours…";
    const r = await api(au(ep), { method: "POST", body: fd });
    qs("#shareUrl", $("shareDialog")).value = shareUrl(r.token);
    qs("#shareResult", $("shareDialog")).hidden = false;
    msg.textContent = "Lien généré avec succès.";
    if (r.qrDataUrl) {
      const c = $("qrCanvas"); const img = new Image();
      img.onload = () => { const ctx = c.getContext("2d"); c.width = img.width; c.height = img.height; ctx.drawImage(img, 0, 0); };
      img.src = r.qrDataUrl;
      qs("#shareQR", $("shareDialog")).hidden = false;
    }
  } catch (e) {
    msg.textContent = "Erreur : " + e.message;
    console.error("Share failed", e);
  } finally {
    setButtonBusy($("confirmShareBtn"), false);
  }
});
$("copyLinkBtn").addEventListener("click", () => {
  const inp = qs("#shareUrl", $("shareDialog")); inp.select(); navigator.clipboard?.writeText(inp.value);
  toast("Lien copié.");
});
$("cancelShareBtn").addEventListener("click", () => { $("shareDialog").close(); S.shareTarget = null; });



// ── Preview ──
function openPreview(f, trigger) {
  const ext = (f.name || "").split(".").pop().toLowerCase();
  const imgExts = ["jpg","jpeg","png","gif","webp","svg","bmp","ico"];
  const vidExts = ["mp4","webm","ogg","mov"];
  const audExts = ["mp3","wav","flac","ogg","m4a","aac"];
  const txtExts = ["txt","md","json","xml","csv","log","ini","cfg","yml","yaml","conf","env","sh","bat","ps1","py","js","ts","css","html","htm","sql","rss","atom"];
  const body = $("previewBody"); const title = $("previewTitle");
  title.textContent = f.name;
  const dlUrl = au(`/files/download?path=${encodeURIComponent(f.path)}`);
  body.replaceChildren();
  if (imgExts.includes(ext)) {
    const image = document.createElement("img");
    image.src = dlUrl;
    image.alt = f.name;
    image.loading = "lazy";
    body.append(image);
  } else if (vidExts.includes(ext) || audExts.includes(ext)) {
    const media = document.createElement(vidExts.includes(ext) ? "video" : "audio");
    media.controls = true;
    media.autoplay = true;
    const source = document.createElement("source");
    source.src = dlUrl;
    media.append(source);
    body.append(media);
  } else if (txtExts.includes(ext)) {
    const pre = document.createElement("pre");
    pre.textContent = "Chargement…";
    body.append(pre);
    fetch(dlUrl)
      .then(r => {
        if (!r.ok) throw new Error("Lecture impossible");
        return r.text();
      })
      .then(text => { pre.textContent = text; })
      .catch(() => { pre.textContent = "Impossible de lire le fichier."; });
  } else {
    const message = document.createElement("p");
    message.append("Aperçu non disponible pour ce type de fichier. ");
    const download = document.createElement("a");
    download.href = dlUrl;
    download.target = "_blank";
    download.rel = "noopener noreferrer";
    download.textContent = "Télécharger";
    message.append(download);
    body.append(message);
  }
  openDialog($("previewDialog"), trigger);
}
// ── Dialogs ──
function openRename(f, trigger) { S.renameTarget = f; $("renameInput").value = f.name; qs("#renameMessage", $("renameDialog")).textContent = ""; openDialog($("renameDialog"), trigger); }
function openDelete(f, trigger) { S.deleteTarget = f; qs("#deleteName", $("deleteDialog")).textContent = f.name; openDialog($("deleteDialog"), trigger); }

// ── Events ──
$("btnUpload").addEventListener("click", (event) => openDialog($("uploadDialog"), event.currentTarget));
$("btnMkdir").addEventListener("click", (event) => { $("mkdirNameInput").value = ""; qs("#mkdirMessage", $("mkdirDialog")).textContent = ""; openDialog($("mkdirDialog"), event.currentTarget); });
$("btnShareFolder").addEventListener("click", (event) => openShareCurrentFolder(event.currentTarget));
$("btnSync").addEventListener("click", async () => {
  setButtonBusy($("btnSync"), true);
  try { await api(au("/files/refresh"), { method: "POST" }); toast("Cache actualisé."); loadFiles(); } catch (e) { showError(e); }
  finally { setButtonBusy($("btnSync"), false); }
});

$("headerSyncButton")?.addEventListener("click", () => {
  $("btnSync").click();
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

// Cibles de dépôt : Racine / Parent
makeDropTarget(qs('.cloud-view-nav .nav-btn[data-nav="root"]'), "");
makeDropTarget(qs('.cloud-view-nav .nav-btn[data-nav="parent"]'), () => S.path.split("/").filter(Boolean).slice(0, -1).join("/"));

// Dépôt de fichiers du bureau sur toute la vue Fichiers (upload)
(function wireFilesViewUpload() {
  const view = $("filesView");
  if (!view) return;
  let depth = 0;
  const hasFiles = (e) => e.dataTransfer && e.dataTransfer.types && Array.from(e.dataTransfer.types).includes("Files");
  view.addEventListener("dragenter", (e) => {
    if (dragItems.length || !hasFiles(e)) return;
    e.preventDefault();
    depth++;
    view.classList.add("upload-target");
  });
  view.addEventListener("dragleave", () => {
    if (dragItems.length) return;
    depth = Math.max(0, depth - 1);
    if (depth === 0) view.classList.remove("upload-target");
  });
  view.addEventListener("dragover", (e) => {
    if (dragItems.length || !hasFiles(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  });
  view.addEventListener("drop", (e) => {
    if (dragItems.length) return;
    depth = 0;
    view.classList.remove("upload-target");
    if (hasFiles(e) && e.dataTransfer.files.length) {
      e.preventDefault();
      startUpload(e.dataTransfer.files);
    }
  });
})();

function switchView(v) {
  S.view = v; updateUrl();
  ["files","history","links","stats"].forEach(x => {
    const el = $(x + "View"); if (el) el.hidden = x !== v;
  });
  qsa(".cloud-view-nav .nav-btn").forEach(b => {
    const isActive = b.dataset.nav === v || (v === "files" && b.dataset.nav === "root");
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", String(isActive));
  });
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
    else openPreview(f);
  }
  if (e.key === "Delete" && S.focusedIdx >= 0) openDelete(items[S.focusedIdx]);
  if (e.key === "F2" && S.focusedIdx >= 0) { e.preventDefault(); openRename(items[S.focusedIdx]); }
  if (e.key === "a" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); selectAll(); }
});
function scrollToRow() {
  const row = qs(`#fileBody tr:nth-child(${S.focusedIdx + 1})`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

// ── Dialog form handling ──
// Use form submit for Enter key support on action forms
$("mkdirForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmMkdirBtn").click(); });
$("renameForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmRenameBtn").click(); });
$("deleteForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmDeleteBtn").click(); });
$("shareForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmShareBtn").click(); });

// ── Confirm buttons ──
$("confirmMkdirBtn").addEventListener("click", async () => {
  const name = $("mkdirNameInput").value.trim(); if (!name) { qs("#mkdirMessage", $("mkdirDialog")).textContent = "Nom requis."; return; }
  setButtonBusy($("confirmMkdirBtn"), true);
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("name", name);
    await api(au("/files/mkdir"), { method: "POST", body: fd }); toast(`Dossier « ${name} » créé.`); $("mkdirDialog").close(); loadFiles();
  } catch (e) { qs("#mkdirMessage", $("mkdirDialog")).textContent = e.message; }
  finally { setButtonBusy($("confirmMkdirBtn"), false); }
});
$("cancelMkdirBtn").addEventListener("click", () => $("mkdirDialog").close());

$("confirmRenameBtn").addEventListener("click", async () => {
  const n = $("renameInput").value.trim(); if (!n || !S.renameTarget) return;
  setButtonBusy($("confirmRenameBtn"), true);
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("old_name", S.renameTarget.name); fd.append("new_name", n);
    await api(au("/files/rename"), { method: "POST", body: fd }); toast("Élément renommé."); $("renameDialog").close(); S.renameTarget = null; loadFiles();
  } catch (e) { qs("#renameMessage", $("renameDialog")).textContent = e.message; }
  finally { setButtonBusy($("confirmRenameBtn"), false); }
});
$("cancelRenameBtn").addEventListener("click", () => { $("renameDialog").close(); S.renameTarget = null; });

$("confirmDeleteBtn").addEventListener("click", async () => {
  if (!S.deleteTarget) return;
  setButtonBusy($("confirmDeleteBtn"), true);
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("name", S.deleteTarget.name);
    await api(au("/files/delete"), { method: "POST", body: fd }); toast("Élément supprimé."); $("deleteDialog").close(); S.deleteTarget = null; loadFiles();
  } catch (e) { showError(e); }
  finally { setButtonBusy($("confirmDeleteBtn"), false); }
});
$("cancelDeleteBtn").addEventListener("click", () => { $("deleteDialog").close(); S.deleteTarget = null; });

// ── Upload ──
$("uploadZone").addEventListener("click", () => $("fileInput").click());
$("uploadZone").addEventListener("keydown", event => {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  $("fileInput").click();
});
$("uploadZone").addEventListener("dragover", e => { e.preventDefault(); $("uploadZone").classList.add("dragover"); });
$("uploadZone").addEventListener("dragleave", () => $("uploadZone").classList.remove("dragover"));
$("uploadZone").addEventListener("drop", e => { e.preventDefault(); $("uploadZone").classList.remove("dragover"); if (e.dataTransfer.files.length) startUpload(e.dataTransfer.files); });
$("fileInput").addEventListener("change", () => { if ($("fileInput").files.length) { startUpload($("fileInput").files); $("fileInput").value = ""; } });
$("cancelUploadBtn").addEventListener("click", () => { $("uploadDialog").close(); $("uploadProgress").hidden = true; });

async function startUpload(files, targetPath) {
  const destPath = targetPath || S.path;
  $("uploadMessage").textContent = ""; $("uploadProgress").hidden = false;
  S.uploadRows.clear();
  $("uploadProgress").replaceChildren(...Array.from(files).map(f => {
    const r = document.createElement("div"); r.className = "progress-row";
    const name = document.createElement("span"); name.className = "pname"; name.textContent = f.name;
    const track = document.createElement("div"); track.className = "ptrack";
    const fill = document.createElement("div"); fill.className = "pfill"; fill.style.width = "0";
    const status = document.createElement("span"); status.className = "pstatus"; status.textContent = "0%";
    track.append(fill);
    r.append(name, track, status);
    S.uploadRows.set(f, r);
    return r;
  }));
  for (const f of files) await uploadOne(f, destPath);
  toast("Téléversement terminé."); $("uploadProgress").hidden = true; loadFiles();
}

async function uploadOne(file, destPath) {
  const fd = new FormData(); fd.append("path", destPath); fd.append("file", file);
  const row = S.uploadRows.get(file);
  const fill = qs(".pfill", row); const st = qs(".pstatus", row);
  try {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", au("/files/upload")); xhr.setRequestHeader("X-Cloud-Panel-CSRF", S.csrf);
    xhr.upload.addEventListener("progress", e => { if (e.lengthComputable && fill && st) { const p = Math.round(e.loaded / e.total * 100); fill.style.width = p + "%"; st.textContent = p + "%"; } });
    xhr.timeout = 300000;
    await new Promise((res, rej) => {
      xhr.onload = () => xhr.status < 300 ? res() : rej(new Error("Upload echoue (HTTP " + xhr.status + ")"));
      xhr.onerror = () => rej(new Error("Erreur reseau"));
      xhr.ontimeout = () => rej(new Error("Timeout: fichier trop volumineux ou connexion lente"));
      xhr.send(fd);
    });
  } catch (e) { if (st) st.textContent = "Erreur"; showError(e); }
}

// ── Select All ──
$("selectAll").addEventListener("change", selectAll);
$("btnSelectionMode").addEventListener("click", () => setSelectionMode(!S.selectionMode));
document.addEventListener("click", (event) => {
  if (event.target.closest("#fileActionMenu") || event.target.closest(".action-btn")) return;
  closeFileActionMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeFileActionMenu();
});

// ── Sort ──
function sortByHeader(th) {
  const key = th.dataset.sort;
  if (S.sortKey === key) S.sortDir = S.sortDir === "asc" ? "desc" : "asc";
  else { S.sortKey = key; S.sortDir = key === "name" ? "asc" : "desc"; }
  S.page = 1;
  renderSortHeaders(); loadFiles(false); updateUrl();
}
qsa(".sortable").forEach(th => {
  th.addEventListener("click", () => sortByHeader(th));
  th.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    sortByHeader(th);
  });
});
function renderSortHeaders() {
  qsa(".sortable").forEach(th => {
    const active = th.dataset.sort === S.sortKey;
    th.classList.toggle("asc", active && S.sortDir === "asc");
    th.classList.toggle("desc", active && S.sortDir === "desc");
    th.setAttribute("aria-sort", active ? (S.sortDir === "asc" ? "ascending" : "descending") : "none");
  });
}

// ── Pagination ──
$("prevPageBtn").addEventListener("click", () => changePage(-1));
$("nextPageBtn").addEventListener("click", () => changePage(1));

// ── Search ──
$("searchInput").addEventListener("input", () => {
  S.search = $("searchInput").value.trim();
  S.page = 1;
  clearTimeout(window.cloudSearchTimer);
  window.cloudSearchTimer = setTimeout(() => loadFiles(false), 180);
  updateUrl();
});

// ── Bulk actions ──
$("bulkClear").addEventListener("click", () => setSelectionMode(false));
$("bulkDelete").addEventListener("click", (event) => {
  if (!S.selected.size) return;
  $("bulkDeleteDescription").textContent = `Supprimer ${S.selected.size} élément${S.selected.size > 1 ? "s" : ""} sélectionné${S.selected.size > 1 ? "s" : ""} ?`;
  openDialog($("bulkDeleteDialog"), event.currentTarget);
});
$("cancelBulkDeleteBtn").addEventListener("click", () => $("bulkDeleteDialog").close());
$("bulkDeleteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("bulkDeleteDialog").close();
  const it = [...S.selected]; const ps = $("progressOverlay"); ps.hidden = false; qs("#progressTitle", ps).textContent = "Suppression…";
  qs("#progressBarFill", ps).value = 0;
  let done = 0;
  for (const p of it) {
    try { const fd = new FormData(); const pp = p.split("/").slice(0, -1).join("/"); const nm = p.split("/").pop(); fd.append("path", pp); fd.append("name", nm); await api(au("/files/delete"), { method: "POST", body: fd }); } catch {}
    done++; qs("#progressBarFill", ps).value = done / it.length; qs("#progressStatus", ps).textContent = `${done}/${it.length}`;
  }
  ps.hidden = true; S.selected.clear(); S.selectedItems.clear(); toast("Suppression terminée."); loadFiles();
});
$("bulkShare").addEventListener("click", async () => {
  if (!S.selected.size) return;
  const it = [...S.selected]; const ps = $("progressOverlay"); ps.hidden = false; qs("#progressTitle", ps).textContent = "Génération de liens…";
  qs("#progressBarFill", ps).value = 0;
  let results = []; let errors = 0; let lastErr = "";
  for (const p of it) {
    try { const r = await api(au("/share/file"), { method: "POST", body: new URLSearchParams({ path: p, expiry_days: "7", password: "" }) }); results.push(r); } catch (e) { errors++; lastErr = e.message; console.error("Share failed for", p, e); }
    qs("#progressBarFill", ps).value = (results.length + errors) / it.length;
    qs("#progressStatus", ps).textContent = `${results.length} OK, ${errors} erreur(s)`;
  }
  ps.hidden = true;
  if (results.length) {
    const msg = results.map(r => shareUrl(r.token)).join("\n");
    navigator.clipboard?.writeText(msg);
  }
  const detail = errors && lastErr ? ` — Derniere erreur: ${lastErr}` : "";
  toast(`${results.length} lien${results.length > 1 ? "s" : ""} généré${results.length > 1 ? "s" : ""}${errors ? `, ${errors} erreur${errors > 1 ? "s" : ""}${detail}` : ""}${results.length ? " et copié(s)" : ""}.`);
});
$("bulkShareFolder").addEventListener("click", (event) => {
  const selectedFolder = getSingleSelectedFolder();
  if (!selectedFolder) return;
  openShare(selectedFolder, event.currentTarget);
});
$("bulkDownload").addEventListener("click", async () => {
  const it = [...S.selected]; if (!it.length) return;
  if (it.length === 1) {
    window.location.href = au(`/files/download?path=${encodeURIComponent(it[0])}`);
    return;
  }
  try {
    const fd = new FormData(); fd.append("paths", it.join("\n"));
    const blob = await fetch(au("/files/download-zip"), { method: "POST", headers: { "X-Cloud-Panel-CSRF": S.csrf }, body: fd }).then(r => { if (!r.ok) throw new Error("Erreur ZIP"); return r.blob(); });
    const url = URL.createObjectURL(blob); const a = document.createElement("a"); a.href = url; a.download = "cloud-panel-bulk.zip"; a.click(); URL.revokeObjectURL(url);
    toast("Archive ZIP téléchargée.");
  } catch (e) { showError(e); }
});

// ── History ──
async function loadHistory() {
  try { const d = await api(au("/history/data")); const items = d.items || [];
    const b = $("historyBody"); if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "table-empty-cell";
      cell.textContent = "Aucun historique.";
      row.append(cell);
      b.replaceChildren(row);
      return;
    }
    b.replaceChildren(...items.map(h => {
      const tr = document.createElement("tr");
      const date = document.createElement("td"); date.className = "date-cell"; date.textContent = fmtDate(h.date);
      const filename = document.createElement("td"); filename.textContent = h.filename || "";
      const size = document.createElement("td"); size.className = "size-cell"; size.textContent = fmtSize(h.size_bytes);
      const action = document.createElement("td"); action.textContent = h.action || "";
      const linkCell = document.createElement("td");
      if (h.token) {
        const link = document.createElement("a");
        link.href = rt(`/download/${encodeURIComponent(h.token)}`);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Lien";
        linkCell.append(link);
      } else {
        linkCell.textContent = "—";
      }
      tr.append(date, filename, size, action, linkCell);
      return tr;
    }));
  } catch (e) { showError(e); }
}

// ── Links ──
async function loadLinks() {
  try { const d = await api(au("/links")); const items = d.items || [];
    const b = $("linksBody"); if (!items.length) {
      const row = document.createElement("tr");
      row.className = "links-empty-row";
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.className = "table-empty-cell";
      cell.textContent = "Aucun lien.";
      row.append(cell);
      b.replaceChildren(row);
      return;
    }
    b.replaceChildren(...items.map(l => {
      const tr = document.createElement("tr");
      const expired = l.expires_at && l.expires_at < Date.now() / 1000;
      const status = l.is_revoked ? "Révoqué" : expired ? "Expiré" : "Actif";
      const filename = document.createElement("td");
      filename.className = "link-file-cell"; filename.dataset.label = "Fichier"; filename.textContent = l.filename || "";
      const token = document.createElement("td");
      token.className = "link-token-cell"; token.dataset.label = "Jeton"; token.textContent = `${String(l.token || "").slice(0, 16)}…`;
      const downloads = document.createElement("td");
      downloads.dataset.label = "Téléchargements"; downloads.textContent = String(l.download_count ?? 0);
      const expiry = document.createElement("td");
      expiry.className = "date-cell"; expiry.dataset.label = "Expire"; expiry.textContent = l.expires_at ? fmtDate(l.expires_at) : "Jamais";
      const statusCell = document.createElement("td");
      statusCell.dataset.label = "Statut";
      statusCell.className = `status-text ${l.is_revoked ? "is-error" : expired ? "is-warning" : "is-success"}`;
      statusCell.textContent = status;
      const acts = document.createElement("td");
      acts.className = "action-cell force link-actions-cell"; acts.dataset.label = "Actions";
      tr.append(filename, token, downloads, expiry, statusCell, acts);
      if (!l.is_revoked && !expired) {
        const rv = document.createElement("button"); rv.className = "action-btn danger"; rv.setAttribute("aria-label", "Revoguer");
        rv.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 7.75h14M9 7.75V5.5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2.25"/></svg>';
        rv.addEventListener("click", async () => { await api(au("/links/revoke"), { method: "POST", body: new URLSearchParams({ token: l.token }) }); toast("Lien révoqué."); loadLinks(); });
        acts.append(rv);
        const ex = document.createElement("button"); ex.className = "action-btn"; ex.setAttribute("aria-label", "Prolonger");
        ex.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"/></svg>';
        ex.addEventListener("click", async () => { await api(au("/links/extend"), { method: "POST", body: new URLSearchParams({ token: l.token, days: "7" }) }); toast("Lien prolonge de 7 jours."); loadLinks(); });
        acts.append(ex);
      }
      const cp = document.createElement("button"); cp.className = "action-btn"; cp.setAttribute("aria-label", "Copier lien");
      cp.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>';
      cp.addEventListener("click", () => { navigator.clipboard?.writeText(shareUrl(l.token)); toast("Lien copié."); });
      acts.append(cp);
      return tr;
    }));
  } catch (e) { showError(e); }
}

// ── Stats ──
async function loadStats() {
  try { const d = await api(au("/stats")); const g = $("statsGrid");
    const cards = [
      ["total_links", "Liens créés"], ["active_links", "Liens actifs"], ["expired_links", "Liens expirés"],
      ["revoked_links", "Liens révoqués"], ["total_downloads", "Téléchargements"], ["total_history", "Téléversements"], ["total_favorites", "Favoris"],
    ];
    g.replaceChildren(...cards.map(([k, l]) => {
      const c = document.createElement("div"); c.className = "stat-card";
      const value = document.createElement("div"); value.className = "stat-value"; value.textContent = String(d[k] ?? "—");
      const label = document.createElement("div"); label.className = "stat-label"; label.textContent = l;
      c.append(value, label);
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
  S.search = u.searchParams.get("search") || "";
  S.sortKey = ["name", "size", "date"].includes(u.searchParams.get("sort")) ? u.searchParams.get("sort") : "name";
  S.sortDir = u.searchParams.get("direction") === "desc" ? "desc" : "asc";
  S.page = Math.max(1, Number.parseInt(u.searchParams.get("page") || "1", 10) || 1);
  $("searchInput").value = S.search;
  renderSortHeaders();
  await refreshSession();
  await loadFavs();
  switchView(v);
}
init();
