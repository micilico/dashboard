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
  diskUsed: "", diskTotal: "", diskPct: 0, diskAvailable: true, totalItems: 0,
  uploadRows: new Map(),
  folderSizes: new Map(),
  clipboard: { mode: null, items: [] },
  viewMode: "list", showHidden: false, globalSearch: false, lastRangeIdx: -1,
  trash: [], autoRefreshTimer: null, uploadJobs: [],
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
  if (S.viewMode === "grid") u.searchParams.set("viewmode", "grid"); else u.searchParams.delete("viewmode");
  if (S.search) u.searchParams.set("search", S.search); else u.searchParams.delete("search");
  if (S.globalSearch) u.searchParams.set("global", "1"); else u.searchParams.delete("global");
  if (S.sortKey !== "name") u.searchParams.set("sort", S.sortKey); else u.searchParams.delete("sort");
  if (S.sortDir !== "asc") u.searchParams.set("direction", S.sortDir); else u.searchParams.delete("direction");
  if (S.page > 1) u.searchParams.set("page", String(S.page)); else u.searchParams.delete("page");
  window.history.replaceState({}, "", u.toString());
}

// ── Load files ──
function visibleItems() {
  if (!S.showHidden) return S.files.filter(f => !f.name.startsWith("."));
  return S.files;
}

async function loadFiles(append) {
  if (S.loading) return; S.loading = true;
  const el = $("scrollSentinel");
  if (!append) { el.classList.remove("loading"); S.files = []; renderFileSkeleton(); }
  try {
    const offset = (S.page - 1) * S.pageSize;
    let d;
    if (S.globalSearch && S.search) {
      const params = new URLSearchParams({ q: S.search, path: S.path, offset: String(offset), limit: String(S.pageSize) });
      d = await api(au(`/files/search?${params.toString()}`));
    } else {
      const params = new URLSearchParams({ path: S.path, offset: String(offset), limit: String(S.pageSize) });
      if (S.search) params.set("search", S.search);
      d = await api(au(`/files?${params.toString()}`));
    }
    S.diskUsed = d.disk_used || ""; S.diskTotal = d.disk_total || ""; S.diskPct = d.disk_percent || 0;
    S.diskAvailable = d.disk_available !== false;
    const items = d.items || [];
    S.files = append ? S.files.concat(items) : items;
    S.allFiles = S.files;
    items.forEach(f => {
      if (!f.is_dir) return;
      const s = S.folderSizes.get(f.path);
      if (s) { f.size = s.size; f.size_bytes = s.size_bytes; }
    });
    S.totalItems = Number(d.total) || items.length;
    S.hasMore = Boolean(d.has_more);
    renderSidebarDisk();
    renderFiles();
    if (S.hasMore) { el.classList.add("loading"); startObserving(); } else { el.classList.remove("loading"); stopObserving(); }
  } catch (e) { $("fileBody").replaceChildren(); showError(e); }
  S.loading = false;
}

// ── Infinite scroll ──
function startObserving() {
  stopObserving();
  S.obs = new IntersectionObserver(([e]) => {
    if (e.isIntersecting && S.hasMore && !S.loading) { S.page++; loadFiles(true); }
  }, { rootMargin: "200px" });
  S.obs.observe($("scrollSentinel"));
}
function stopObserving() { if (S.obs) { S.obs.disconnect(); S.obs = null; } }

// ── Sort & filter ──
function fileTypeLabel(f) {
  if (f.is_dir) return "Dossier";
  const t = fileIcon(f.name, false);
  const labels = { video: "Vidéo", audio: "Audio", image: "Image", pdf: "PDF", archive: "Archive", document: "Document", file: "Fichier" };
  return labels[t] || "Fichier";
}

function getSortedFiltered() {
  let items = visibleItems();
  if (S.search && !S.globalSearch) items = items.filter(f => f.name.toLowerCase().includes(S.search.toLowerCase()));
  const key = S.sortKey, dir = S.sortDir === "asc" ? 1 : -1;
  items = [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    if (key === "name") return a.name.localeCompare(b.name, "fr") * dir;
    if (key === "size") return ((a.size_bytes || 0) - (b.size_bytes || 0)) * dir;
    if (key === "date") return ((a.modified || 0) - (b.modified || 0)) * dir;
    if (key === "created") return ((a.created || 0) - (b.created || 0)) * dir;
    if (key === "type") return fileTypeLabel(a).localeCompare(fileTypeLabel(b), "fr") * dir;
    return 0;
  });
  return items;
}

// ── Render ──
function rememberFolderSize(f, d) {
  f.size = d.size;
  f.size_bytes = d.size_bytes;
  S.folderSizes.set(f.path, { size: d.size, size_bytes: d.size_bytes });
}

function folderSizeCell(f) {
  if (!f.is_dir) {
    const span = document.createElement("span");
    span.textContent = f.size || fmtSize(f.size_bytes);
    return span;
  }
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "size-btn";
  btn.textContent = f.size_bytes ? f.size : "—";
  btn.setAttribute("aria-label", f.size_bytes ? `Taille de ${f.name} : ${f.size}. Recalculer` : `Calculer la taille de ${f.name}`);
  btn.title = "Calculer la taille du dossier";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    btn.textContent = "…";
    try {
      const pp = f.path.split("/").slice(0, -1).join("/");
      const nm = f.path.split("/").pop();
      const fd = new FormData();
      fd.append("path", pp);
      fd.append("name", nm);
      const d = await api(au("/files/size"), { method: "POST", body: fd, timeout: 600000 });
      rememberFolderSize(f, d);
      btn.textContent = d.size;
      btn.setAttribute("aria-label", `Taille de ${f.name} : ${d.size}. Recalculer`);
    } catch (e) {
      btn.textContent = f.size_bytes ? f.size : "—";
      toast(describeError(e, "Calcul de la taille impossible."));
    } finally {
      btn.disabled = false;
      btn.removeAttribute("aria-busy");
    }
  });
  return btn;
}

async function calcAllFolderSizes() {
  const dirs = S.allFiles.filter(f => f.is_dir);
  if (!dirs.length) { toast("Aucun dossier affiché."); return; }
  const btn = $("btnCalcSizes");
  setButtonBusy(btn, true);
  try {
    const fd = new FormData();
    fd.append("paths", dirs.map(f => f.path).join("\n"));
    const d = await api(au("/files/sizes"), { method: "POST", body: fd, timeout: 600000 });
    const byPath = new Map((d.items || []).map(r => [r.path, r]));
    dirs.forEach(f => {
      const r = byPath.get(f.path);
      if (r) rememberFolderSize(f, r);
    });
    renderFiles();
    const ok = (d.items || []).length;
    const failed = Number(d.failed) || 0;
    toast(failed ? `Tailles calculées : ${ok} dossier(s), ${failed} en échec.` : `Tailles de ${ok} dossier(s) calculées.`);
  } catch (e) {
    toast(describeError(e, "Calcul des tailles impossible."));
  } finally {
    setButtonBusy(btn, false);
  }
}

function changePage(delta) {
  const totalPages = Math.max(1, Math.ceil(S.totalItems / S.pageSize));
  S.page = Math.max(1, Math.min(totalPages, S.page + delta));
  loadFiles(false); updateUrl();
}

function renderFileSkeleton() {
  $("emptyState").hidden = true;
  $("paginationBar").hidden = true;
  $("fileBody").replaceChildren(...Array.from({ length: 5 }, () => {
    const row = document.createElement("tr");
    row.className = "skeleton-row";
    const cell = document.createElement("td");
    cell.colSpan = 8;
    const line = document.createElement("span");
    line.className = "skeleton-line";
    cell.append(line);
    row.append(cell);
    return row;
  }));
}

function rangeSelectTo(idx) {
  const items = getSortedFiltered();
  if (S.lastRangeIdx < 0) S.lastRangeIdx = idx;
  const from = Math.min(S.lastRangeIdx, idx);
  const to = Math.max(S.lastRangeIdx, idx);
  S.selectionMode = true;
  for (let i = from; i <= to; i++) {
    const item = items[i];
    if (!item) continue;
    if (!S.selected.has(item.path)) {
      S.selected.add(item.path);
      S.selectedItems.set(item.path, item);
    }
  }
}

function wireRowEvents(tr, f, i) {
  tr.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!S.selected.has(f.path)) {
      S.selected = new Set([f.path]);
      S.selectedItems = new Map([[f.path, f]]);
      S.selectionMode = true;
      renderFiles();
    }
    openFileActionMenu(f, tr.querySelector(".action-btn"));
  });
  tr.addEventListener("dblclick", (e) => {
    if (e.target.closest("button,a,input")) return;
    if (f.is_dir) navigate(f.path);
    else openPreview(f);
  });
  tr.addEventListener("click", (e) => {
    if (e.target.closest("button,a,input")) return;
    if (e.ctrlKey || e.metaKey) { toggleSelect(f); S.lastRangeIdx = i; return; }
    if (e.shiftKey) { rangeSelectTo(i); renderFiles(); renderBulkBar(); return; }
    S.focusedIdx = i;
    if (S.selectionMode) toggleSelect(f);
  });
}

function makeRow(f, i) {
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

  // Type
  const tdType = document.createElement("td"); tdType.className = "type-cell"; tdType.textContent = fileTypeLabel(f);

  // Size
  const td2 = document.createElement("td"); td2.className = "size-cell"; td2.append(folderSizeCell(f));

  // Date
  const td3 = document.createElement("td"); td3.className = "date-cell"; td3.textContent = f.modified ? fmtDate(f.modified) : "—";

  // Created
  const tdCreated = document.createElement("td"); tdCreated.className = "created-cell"; tdCreated.textContent = f.created ? fmtDate(f.created) : "—";

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

  tr.append(td0, td1, tdType, td2, td3, tdCreated, td4, td5);
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
      if (items.length) {
        if (e.ctrlKey || e.metaKey) copyItems(items, f.path);
        else moveItemsTo(f.path, items);
      }
      else if (e.dataTransfer.files && e.dataTransfer.files.length) startUpload(e.dataTransfer.files, f.path);
    });
  }
  wireRowEvents(tr, f, i);
  return tr;
}

function makeTile(f, i) {
  const tile = document.createElement("div");
  tile.className = "grid-tile";
  tile.dataset.path = f.path;
  tile.tabIndex = 0;
  tile.draggable = true;
  if (S.selected.has(f.path)) tile.classList.add("selected");
  const isImg = !f.is_dir && ["jpg","jpeg","png","gif","webp","svg","bmp","ico"].includes((f.name.split(".").pop() || "").toLowerCase());
  const preview = document.createElement("div"); preview.className = "grid-preview";
  if (f.is_dir) {
    preview.innerHTML = `<span class="grid-icon folder">${fileIconSVG("folder")}</span>`;
  } else if (isImg) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = f.name;
    img.src = au(`/files/download?path=${encodeURIComponent(f.path)}`);
    img.addEventListener("error", () => { preview.innerHTML = `<span class="grid-icon ${fileIcon(f.name)}">${fileIconSVG(fileIcon(f.name))}</span>`; });
    preview.append(img);
  } else {
    preview.innerHTML = `<span class="grid-icon ${fileIcon(f.name)}">${fileIconSVG(fileIcon(f.name))}</span>`;
  }
  const meta = document.createElement("div"); meta.className = "grid-meta";
  const name = document.createElement("span"); name.className = "grid-name"; name.textContent = f.name; name.title = f.name;
  const sub = document.createElement("span"); sub.className = "grid-sub";
  sub.textContent = f.is_dir ? fileTypeLabel(f) : `${fmtSize(f.size_bytes)} · ${fileTypeLabel(f)}`;
  meta.append(name, sub);
  tile.append(preview, meta);
  tile.addEventListener("click", (e) => {
    if (e.ctrlKey || e.metaKey) { toggleSelect(f); S.lastRangeIdx = i; return; }
    if (e.shiftKey) { rangeSelectTo(i); renderFiles(); renderBulkBar(); return; }
    if (S.selectionMode) toggleSelect(f);
    else if (f.is_dir) navigate(f.path);
    else openPreview(f);
  });
  tile.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!S.selected.has(f.path)) { S.selected = new Set([f.path]); S.selectedItems = new Map([[f.path, f]]); S.selectionMode = true; renderFiles(); }
    openFileActionMenu(f, tile);
  });
  tile.addEventListener("dragstart", (e) => dragPayload(e, f));
  tile.addEventListener("dragend", clearDrag);
  if (f.is_dir) {
    tile.addEventListener("dragover", (e) => {
      if (!dragItems.length) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      tile.classList.add("drop-target");
    });
    tile.addEventListener("dragleave", () => tile.classList.remove("drop-target"));
    tile.addEventListener("drop", (e) => {
      e.preventDefault();
      tile.classList.remove("drop-target");
      const items = readDragItems(e);
      const dragged = items.length ? items : dragItems;
      if (dragged.length) {
        if (e.ctrlKey || e.metaKey) copyItems(dragged, f.path);
        else moveItemsTo(f.path, dragged);
      }
    });
  }
  return tile;
}

function renderFiles() {
  const all = getSortedFiltered();
  const totalPages = Math.max(1, Math.ceil(S.totalItems / S.pageSize));
  S.page = Math.min(S.page, totalPages);
  const empty = $("emptyState");
  const sentinel = $("scrollSentinel");
  const pagBar = $("paginationBar");
  const tableWrap = $("filesView").querySelector(".table-wrap");
  const grid = $("fileGrid");
  $("filesView").classList.toggle("selection-mode", S.selectionMode);
  const selectionButton = $("btnSelectionMode");
  if (selectionButton) {
    selectionButton.setAttribute("aria-pressed", S.selectionMode ? "true" : "false");
    selectionButton.classList.toggle("active", S.selectionMode);
    const label = qs("span", selectionButton);
    if (label) label.textContent = S.selectionMode ? "Terminer" : "Sélectionner";
  }
  const viewToggle = $("btnViewToggle");
  if (viewToggle) viewToggle.setAttribute("aria-pressed", S.viewMode === "grid" ? "true" : "false");
  const hiddenToggle = $("btnHiddenToggle");
  if (hiddenToggle) hiddenToggle.setAttribute("aria-pressed", S.showHidden ? "true" : "false");
  closeFileActionMenu();

  if (all.length === 0) {
    if (tableWrap) tableWrap.hidden = false;
    if (grid) grid.hidden = true;
    $("fileBody").replaceChildren();
    empty.hidden = false;
    qs("p", empty).textContent = S.search ? "Aucun résultat." : "Ce dossier est vide.";
    sentinel.classList.remove("loading"); pagBar.hidden = true; renderBreadcrumb(); renderBulkBar(); return;
  }
  empty.hidden = true;

  if (S.viewMode === "grid") {
    if (tableWrap) tableWrap.hidden = true;
    grid.hidden = false;
    grid.replaceChildren(...all.map((f, i) => makeTile(f, i)));
  } else {
    if (tableWrap) tableWrap.hidden = false;
    grid.hidden = true;
    $("fileBody").replaceChildren(...all.map((f, i) => makeRow(f, i)));
  }

  renderBreadcrumb();
  renderBulkBar();

  // Pagination
  if (S.totalItems > S.pageSize && S.search && !S.globalSearch) {
    pagBar.hidden = false;
    $("pageInfo").textContent = `Page ${S.page} / ${totalPages} · ${S.totalItems} élément${S.totalItems > 1 ? "s" : ""}`;
    $("prevPageBtn").disabled = S.page <= 1;
    $("nextPageBtn").disabled = S.page >= totalPages;
  } else {
    pagBar.hidden = true;
  }
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
  qsa(".grid-tile").forEach(tile => {
    if (dragItems.some(i => i.path === tile.dataset.path)) tile.classList.add("dragging");
  });
}

function clearDrag() {
  // Keep the payload alive for the drop event: some browsers emit dragend
  // immediately before the target's drop handler completes.
  const endedItems = dragItems;
  setTimeout(() => {
    if (dragItems !== endedItems) return;
    qsa("#fileBody tr.dragging, .grid-tile.dragging").forEach(el => el.classList.remove("dragging"));
    dragItems = [];
  }, 0);
}

function readDragItems(e) {
  const raw = e.dataTransfer.getData("application/x-cloud-item");
  if (!raw) return dragItems;
  try {
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : dragItems;
  } catch {
    return dragItems;
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
    const types = e.dataTransfer?.types ? Array.from(e.dataTransfer.types) : [];
    if (!dragItems.length && !types.includes("application/x-cloud-item")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    el.classList.add("drop-active");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drop-active"));
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    el.classList.remove("drop-active");
    const items = readDragItems(e);
    if (items.length) {
      const dest = resolveDest();
      if (e.ctrlKey || e.metaKey) copyItems(items, dest);
      else moveItemsTo(dest, items);
    }
  });
}

function renderSidebarDisk() {
  const el = $("sidebarDisk");
  if (S.diskAvailable && S.diskTotal && S.diskTotal !== "N/A") {
    el.hidden = false;
    const pct = Math.min(100, Math.max(0, S.diskPct));
    qs(".sidebar-disk-fill", el).style.width = `${pct}%`;
    qs(".sidebar-disk-fill", el).style.background = pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warning)" : "var(--success)";
    qs(".sidebar-disk-text", el).textContent = `${S.diskUsed} / ${S.diskTotal}`;
    window.DashboardDOM?.updateDiskRing({ percent: pct, usedLabel: S.diskUsed, totalLabel: S.diskTotal });
  } else if (S.diskAvailable === false) {
    el.hidden = false;
    qs(".sidebar-disk-fill", el).style.width = "0%";
    qs(".sidebar-disk-text", el).textContent = "Indisponible";
    window.DashboardDOM?.hideDiskRing();
  } else {
    el.hidden = true;
    window.DashboardDOM?.hideDiskRing();
  }
}

function renderBulkBar() {
  const el = $("bulkBar"); const n = S.selected.size;
  const hasClip = S.clipboard.items.length > 0;
  if (n === 0 && !hasClip) { el.hidden = true; return; }
  if (n > 0) S.selectionMode = true;
  el.hidden = false;
  qs("#bulkCount", el).textContent = n > 0 ? `${n} sélectionné${n > 1 ? "s" : ""}` : `Presse-papiers (${S.clipboard.items.length})`;
  $("bulkPaste").hidden = !hasClip;
  const selectedFolder = getSingleSelectedFolder();
  $("bulkShareFolder").hidden = !selectedFolder;
  ["bulkCopy", "bulkShare", "bulkDelete"].forEach(id => { $(id).hidden = n === 0; });
  const clearBtn = $("bulkClear");
  clearBtn.hidden = false;
  clearBtn.textContent = n > 0 ? "Désélectionner" : "Vider le presse-papiers";
  if (n > 0 && !hasClip) $("bulkCopy").hidden = false;
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
    if (isEditableText(file.name)) items.push(menuItem("Modifier", '<path d="M15.25 5.25 18.75 8.75M7.75 16.25l-1 2 2-1L16 10 14 8Z"/>', () => openEditor(file, invoker)));
  }
  items.push(menuItem("Partager", '<path d="M13.5 10.5 10.5 13.5M8.5 15.5l-1.5 1.5a3 3 0 0 0 4.25 4.25l3-3a3 3 0 0 0 0-4.24M15.5 8.5l1.5-1.5a3 3 0 0 0-4.25-4.25l-3 3a3 3 0 0 0 0 4.24"/>', () => openShare(file, invoker)));
  items.push(menuItem("Renommer", '<path d="M15.25 5.25 18.75 8.75M7.75 16.25l-1 2 2-1L16 10 14 8Z"/>', () => openRename(file, invoker)));
  items.push(menuItem("Copier", '<path d="M9 9h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2zM15 4a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v10"/>', () => copyToClipboard([file])));
  items.push(menuItem("Dupliquer", '<path d="M9 9h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2zM15 4a2 2 0 0 0-2-2H7a2 2 0 0 0-2 2v10"/>', async () => {
    const src = file.path.split("/").slice(0, -1).join("/");
    await copyItems([file], src);
  }));
  items.push(menuItem("Sélectionner", '<path d="M9 11.5 11 13.5 15.5 8.5"/><rect x="4" y="4" width="16" height="16" rx="3"/>', () => toggleSelect(file)));
  items.push(menuItem("Propriétés", '<circle cx="12" cy="12" r="8.25"/><path d="M12 11v5M12 8h.01"/>', () => openProperties(file, invoker)));
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
function openDelete(f, trigger) {
  S.deleteTarget = f;
  qs("#deleteName", $("deleteDialog")).textContent = f.name;
  $("deletePermanent").checked = false;
  openDialog($("deleteDialog"), trigger);
}

// ── Events ──
$("btnUpload").addEventListener("click", (event) => openDialog($("uploadDialog"), event.currentTarget));
$("btnMkdir").addEventListener("click", (event) => { $("mkdirNameInput").value = ""; qs("#mkdirMessage", $("mkdirDialog")).textContent = ""; openDialog($("mkdirDialog"), event.currentTarget); });
$("btnShareFolder").addEventListener("click", (event) => openShareCurrentFolder(event.currentTarget));
$("btnCalcSizes").addEventListener("click", calcAllFolderSizes);
$("btnSync").addEventListener("click", async () => {
  setButtonBusy($("btnSync"), true);
  try { await api(au("/files/refresh"), { method: "POST" }); toast("Cache actualisé."); loadFiles(); } catch (e) { showError(e); }
  finally { setButtonBusy($("btnSync"), false); }
});

$("headerSyncButton")?.addEventListener("click", () => {
  $("btnSync").click();
});

// ── Ranger les médias ──
$("btnOrganizeSeries").addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  const msg = qs("#organizeMessage", $("organizeDialog"));
  msg.textContent = "";
  setButtonBusy(btn, true);
  try {
    const fd = new FormData();
    fd.append("path", S.path);
    const d = await api(au("/files/organize/preview"), { method: "POST", body: fd });
    renderOrganizePreview(d);
    openDialog($("organizeDialog"), btn);
  } catch (e) {
    msg.textContent = e.message;
  } finally {
    setButtonBusy(btn, false);
  }
});

function organizeRowCell(source, isDir) {
  const ic = document.createElement("span");
  ic.className = `file-icon ${isDir ? "folder" : fileIcon(source, false)}`;
  ic.innerHTML = fileIconSVG(fileIcon(source, isDir));
  const from = document.createElement("span");
  from.className = "organize-from";
  from.textContent = source;
  from.title = source;
  const arrow = document.createElement("span");
  arrow.className = "organize-arrow";
  arrow.setAttribute("aria-hidden", "true");
  arrow.textContent = "→";
  const to = document.createElement("span");
  to.className = "organize-to";
  to.textContent = "";
  const li = document.createElement("li");
  li.append(ic, from, arrow, to);
  return { li, to };
}

function organizeSection(title, items, renderRow) {
  const sec = document.createElement("div");
  sec.className = "organize-section";
  sec.setAttribute("role", "listitem");
  const head = document.createElement("div");
  head.className = "organize-section-head";
  const label = document.createElement("span");
  label.className = "organize-section-title";
  label.textContent = title;
  const count = document.createElement("span");
  count.className = "organize-section-count";
  count.textContent = String(items.length);
  head.append(label, count);
  const ul = document.createElement("ul");
  ul.className = "organize-section-items";
  items.forEach(renderRow);
  sec.append(head, ul);
  return sec;
}

function renderOrganizePreview(d) {
  const series = d.series || [];
  const movies = d.movies || [];
  const parasites = d.parasites || [];
  const duplicates = d.duplicates || [];
  const totals = d.totals || {};
  const summary = $("organizeSummary");
  const sections = $("organizeSections");
  const confirmBtn = $("confirmOrganizeBtn");
  sections.replaceChildren();
  const parts = [];
  if (series.length) parts.push(`${series.length} série${series.length > 1 ? "s" : ""}`);
  if (movies.length) parts.push(`${movies.length} film${movies.length > 1 ? "s" : ""}`);
  if (parasites.length) parts.push(`${parasites.length} parasite${parasites.length > 1 ? "s" : ""}`);
  if (duplicates.length) parts.push(`${duplicates.length} doublon${duplicates.length > 1 ? "s" : ""}`);
  if (!parts.length) {
    summary.textContent = "Aucun média détecté dans ce dossier.";
    confirmBtn.disabled = true;
    return;
  }
  summary.textContent = parts.join(" · ");
  confirmBtn.disabled = false;

  if (series.length) {
    const sec = organizeSection("Séries", series, (g) => {
      const block = document.createElement("li");
      block.className = "organize-group-block";
      const ghead = document.createElement("div");
      ghead.className = "organize-group-head";
      const name = document.createElement("span");
      name.className = "organize-group-name";
      name.textContent = g.name;
      name.title = g.name;
      const badge = document.createElement("span");
      badge.className = "organize-group-badge";
      badge.textContent = g.folder_exists ? "fusion" : "création";
      ghead.append(name, badge);
      const sub = document.createElement("ul");
      sub.className = "organize-group-items";
      (g.items || []).forEach(entry => {
        const { li, to } = organizeRowCell(entry.name, entry.is_dir);
        to.textContent = entry.target;
        to.title = entry.target;
        sub.append(li);
      });
      block.append(ghead, sub);
      return block;
    });
    qs(".organize-section-count", sec).textContent = String(totals.series_items ?? series.length);
    sections.append(sec);
  }

  if (movies.length) {
    sections.append(organizeSection("Films", movies, (m) => {
      const { li, to } = organizeRowCell(m.name, m.is_dir);
      to.textContent = m.target;
      to.title = m.target;
      return li;
    }));
  }

  if (parasites.length) {
    sections.append(organizeSection("Parasites signalés", parasites, (p) => {
      const { li, to } = organizeRowCell(p.path, false);
      li.classList.add("organize-parasite");
      to.classList.add("organize-note");
      to.textContent = `signalé (${p.reason}), non supprimé`;
      to.title = p.path;
      return li;
    }));
  }

  if (duplicates.length) {
    sections.append(organizeSection("Doublons détectés", duplicates, (duplicate) => {
      const { li, to } = organizeRowCell(duplicate.file, false);
      li.classList.add("organize-duplicate");
      to.classList.add("organize-note");
      to.textContent = `${duplicate.status} → ${duplicate.target}`;
      to.title = duplicate.target;
      return li;
    }));
  }
}

$("organizeForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmOrganizeBtn").click(); });
$("cancelOrganizeBtn").addEventListener("click", () => $("organizeDialog").close());
$("confirmOrganizeBtn").addEventListener("click", async () => {
  const btn = $("confirmOrganizeBtn");
  setButtonBusy(btn, true);
  try {
    const fd = new FormData();
    fd.append("path", S.path);
    const r = await api(au("/files/organize/apply"), { method: "POST", body: fd });
    $("organizeDialog").close();
    const sc = Number(r.series_count) || 0;
    const sm = Number(r.series_moved) || 0;
    const mm = Number(r.movies_moved) || 0;
    const parts = [];
    if (sm) parts.push(`${sm} saison${sm > 1 ? "s" : ""}`);
    if (mm) parts.push(`${mm} film${mm > 1 ? "s" : ""}`);
    const moved = sm + mm;
    toast(parts.length
      ? `${parts.join(" et ")} rangé${moved > 1 ? "s" : ""}${sc ? ` dans ${sc} série${sc > 1 ? "s" : ""}` : ""}.`
      : "Rien à ranger.");
    if (r.errors && r.errors.length) toast(`Non déplacé${r.errors.length > 1 ? "s" : ""} : ${r.errors.slice(0, 2).join(" · ")}`);
    loadFiles();
  } catch (e) {
    qs("#organizeMessage", $("organizeDialog")).textContent = e.message;
  } finally {
    setButtonBusy(btn, false);
  }
});

// Sidebar nav
qsa("[data-nav]").forEach(b => b.addEventListener("click", () => {
  const v = b.dataset.nav;
  if (v === "root") navigate("");
  else if (v === "parent") { const p = S.path.split("/").filter(Boolean).slice(0, -1).join("/"); navigate(p); }
  else if (v === "history") switchView("history");
  else if (v === "links") switchView("links");
  else if (v === "stats") switchView("stats");
  else if (v === "trash") switchView("trash");
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
  ["files","history","links","stats","trash"].forEach(x => {
    const el = $(x + "View"); if (el) el.hidden = x !== v;
  });
  qsa(".cloud-view-nav .nav-btn").forEach(b => {
    const isActive = b.dataset.nav === v || (v === "files" && b.dataset.nav === "root");
    b.classList.toggle("active", isActive);
    b.setAttribute("aria-selected", String(isActive));
  });
  if (v === "history") { stopAutoRefresh(); loadHistory(); }
  if (v === "links") { stopAutoRefresh(); loadLinks(); }
  if (v === "stats") { stopAutoRefresh(); loadStats(); }
  if (v === "trash") { stopAutoRefresh(); loadTrash(); }
  if (v === "files") { stopObserving(); loadFiles(); startAutoRefresh(); }
}

// ── Keyboard ──
document.addEventListener("keydown", (e) => {
  if ($("uploadDialog").open || $("mkdirDialog").open || $("renameDialog").open || $("deleteDialog").open || $("shareDialog").open || $("previewDialog").open || $("organizeDialog").open || $("touchDialog").open || $("editDialog").open || $("propertiesDialog").open || $("bulkDeleteDialog").open) {
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
  if ((e.key === "c" || e.key === "x") && (e.ctrlKey || e.metaKey) && S.selected.size) {
    e.preventDefault();
    const sel = [...S.selected].map(p => S.selectedItems.get(p) || { path: p, name: p.split("/").pop(), is_dir: false });
    copyToClipboard(sel);
  }
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
    if ($("deletePermanent").checked) fd.append("permanent", "true");
    await api(au("/files/delete"), { method: "POST", body: fd });
    toast($("deletePermanent").checked ? "Élément supprimé définitivement." : "Élément déplacé vers la corbeille.");
    $("deleteDialog").close(); S.deleteTarget = null; loadFiles();
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
  const overwrite = $("uploadRenameConflict").checked ? "rename" : "overwrite";
  $("uploadMessage").textContent = "";
  $("uploadProgress").hidden = false;
  S.uploadRows.clear();
  const list = Array.from(files);
  const jobs = [];
  $("uploadProgress").replaceChildren(...list.map(f => {
    const r = document.createElement("div"); r.className = "progress-row";
    const name = document.createElement("span"); name.className = "pname"; name.textContent = f.name;
    const track = document.createElement("div"); track.className = "ptrack";
    const fill = document.createElement("div"); fill.className = "pfill"; fill.style.width = "0";
    const st = document.createElement("span"); st.className = "pstatus"; st.textContent = "En attente…";
    const controls = document.createElement("span"); controls.className = "pcontrols";
    track.append(fill);
    r.append(name, track, st, controls);
    const job = { row: r, fill, st, controls, done: false, cancelled: false, controller: null, retried: false };
    S.uploadRows.set(f, job);
    jobs.push({ f, job });
    return r;
  }));

  const queue = [...jobs];
  let active = 0, done = 0, ok = 0, errors = 0;
  const CONCURRENCY = 3;

  const finish = () => {
    done++;
    $("uploadProgress").hidden = true;
    toast(errors ? `Téléversement terminé : ${ok} OK, ${errors} erreur(s).` : "Téléversement terminé.");
    loadFiles();
  };

  const run = (job, f) => {
    job.st.textContent = "0%";
    const cancel = document.createElement("button");
    cancel.className = "mini-btn"; cancel.type = "button"; cancel.textContent = "Annuler";
    cancel.addEventListener("click", () => { job.cancelled = true; job.controller?.abort(); job.st.textContent = "Annulé"; });
    job.controls.replaceChildren(cancel);
    uploadOne(f, destPath, overwrite).then(success => {
      active--;
      done++;
      if (success) ok++; else errors++;
      if (!success) addRetry(job, f, destPath, overwrite);
      if (done === list.length) finish();
      else tick();
    });
  };

  const tick = () => {
    while (active < CONCURRENCY && queue.length) {
      const next = queue.shift();
      if (!next) break;
      active++;
      run(next.job, next.f);
    }
  };
  tick();
}

async function uploadOne(file, destPath, overwrite) {
  const job = S.uploadRows.get(file);
  if (!job) return false;
  const fd = new FormData(); fd.append("path", destPath); fd.append("overwrite", overwrite); fd.append("file", file);
  try {
    const controller = new AbortController();
    job.controller = controller;
    const xhr = new XMLHttpRequest();
    xhr.open("POST", au("/files/upload"));
    xhr.setRequestHeader("X-Cloud-Panel-CSRF", S.csrf);
    xhr.upload.addEventListener("progress", e => {
      if (e.lengthComputable && job.fill && !job.cancelled) {
        const p = Math.round(e.loaded / e.total * 100);
        job.fill.style.width = p + "%";
        job.st.textContent = p + "%";
      }
    });
    xhr.timeout = 900000;
    await new Promise((res, rej) => {
      xhr.onload = () => xhr.status < 300 ? res() : rej(new Error("Échec (HTTP " + xhr.status + ")"));
      xhr.onerror = () => rej(new Error("Erreur réseau"));
      xhr.ontimeout = () => rej(new Error("Timeout (fichier volumineux)"));
      controller.signal.addEventListener("abort", () => xhr.abort());
      xhr.send(fd);
    });
    job.st.textContent = "OK";
    job.fill.style.width = "100%";
    job.controls.replaceChildren();
    return true;
  } catch (e) {
    if (job.cancelled) job.st.textContent = "Annulé";
    else job.st.textContent = "Erreur";
    return false;
  }
}

function addRetry(job, file, destPath, overwrite) {
  const retry = document.createElement("button");
  retry.className = "mini-btn"; retry.type = "button"; retry.textContent = "Réessayer";
  retry.addEventListener("click", () => {
    job.st.textContent = "0%"; job.fill.style.width = "0";
    uploadOne(file, destPath, overwrite).then(s => { if (!s) addRetry(job, file, destPath, overwrite); });
  });
  job.controls.replaceChildren(retry);
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
$("globalSearch").addEventListener("change", () => {
  S.globalSearch = $("globalSearch").checked;
  S.page = 1;
  loadFiles(false);
  updateUrl();
  toast(S.globalSearch ? "Recherche globale activée." : "Recherche limitée au dossier courant.");
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
  ps.hidden = true; S.selected.clear(); S.selectedItems.clear(); toast("Suppression terminée (élément(s) envoyé(s) à la corbeille)."); loadFiles();
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

// ── Copier / presse-papiers ──
const EDITABLE_EXTS = ["txt","md","json","xml","csv","log","ini","cfg","yml","yaml","conf","env","sh","bat","ps1","py","js","ts","css","html","htm","sql","rss","atom"];
function isEditableText(name) { return EDITABLE_EXTS.includes((name || "").split(".").pop().toLowerCase()); }

function copyToClipboard(items) {
  S.clipboard = { mode: "copy", items: items.map(i => ({ path: i.path, name: i.name, is_dir: Boolean(i.is_dir) })) };
  toast(`Copié : ${items.length} élément${items.length > 1 ? "s" : ""}. Utilisez « Coller ici ».`);
  renderBulkBar();
}

async function copyItems(items, destPath) {
  let done = 0; const errors = [];
  for (const it of items) {
    const srcDir = it.path.split("/").slice(0, -1).join("/");
    const name = it.name || it.path.split("/").pop();
    const fd = new FormData();
    fd.append("path", srcDir);
    fd.append("name", name);
    fd.append("dest", destPath);
    try {
      await api(au("/files/copy"), { method: "POST", body: fd });
      done++;
    } catch (e) { errors.push(`${name} : ${e.message}`); }
  }
  if (errors.length) toast(`Copie refusée : ${errors.slice(0, 2).join(" · ")}`);
  toast(done ? (done === 1 ? "Élément copié." : `${done} éléments copiés.`) : "Aucune copie effectuée.");
  loadFiles();
}

async function pasteItems() {
  const items = S.clipboard.items;
  if (!items.length) return;
  await copyItems(items, S.path);
}

$("bulkCopy").addEventListener("click", () => {
  if (!S.selected.size) return;
  const items = [...S.selected].map(p => S.selectedItems.get(p) || { path: p, name: p.split("/").pop(), is_dir: false });
  copyToClipboard(items);
});
$("bulkPaste").addEventListener("click", async () => {
  setButtonBusy($("bulkPaste"), true);
  await pasteItems();
  setButtonBusy($("bulkPaste"), false);
});
$("bulkClear").addEventListener("click", () => {
  if (S.selected.size) setSelectionMode(false);
  else { S.clipboard = { mode: null, items: [] }; toast("Presse-papiers vidé."); renderBulkBar(); }
});

// ── Nouveau fichier / éditeur ──
$("btnTouch").addEventListener("click", (event) => {
  $("touchNameInput").value = "";
  qs("#touchMessage", $("touchDialog")).textContent = "";
  openDialog($("touchDialog"), event.currentTarget);
});
$("touchForm").addEventListener("submit", (e) => { e.preventDefault(); $("confirmTouchBtn").click(); });
$("confirmTouchBtn").addEventListener("click", async () => {
  const name = $("touchNameInput").value.trim();
  if (!name) { qs("#touchMessage", $("touchDialog")).textContent = "Nom requis."; return; }
  setButtonBusy($("confirmTouchBtn"), true);
  try {
    const fd = new FormData(); fd.append("path", S.path); fd.append("name", name);
    await api(au("/files/touch"), { method: "POST", body: fd });
    toast(`Fichier « ${name} » créé.`);
    $("touchDialog").close();
    loadFiles();
  } catch (e) { qs("#touchMessage", $("touchDialog")).textContent = e.message; }
  finally { setButtonBusy($("confirmTouchBtn"), false); }
});
$("cancelTouchBtn").addEventListener("click", () => $("touchDialog").close());

function openEditor(f, trigger) {
  S.editTarget = f;
  $("editTitle").textContent = f.name;
  $("editContent").value = "Chargement…";
  qs("#editMessage", $("editDialog")).textContent = "";
  openDialog($("editDialog"), trigger);
  loadEditContent(f);
}
async function loadEditContent(f) {
  try {
    const d = await api(au(`/files/content?path=${encodeURIComponent(f.path)}`));
    $("editContent").value = d.content || "";
  } catch (e) {
    qs("#editMessage", $("editDialog")).textContent = e.message;
    $("editContent").value = "";
  }
}
$("saveEditBtn").addEventListener("click", async () => {
  if (!S.editTarget) return;
  const btn = $("saveEditBtn");
  setButtonBusy(btn, true);
  try {
    const fd = new FormData();
    fd.append("path", S.editTarget.path);
    fd.append("content", $("editContent").value);
    await api(au("/files/write"), { method: "POST", body: fd });
    toast("Fichier enregistré.");
    $("editDialog").close();
    loadFiles();
  } catch (e) { qs("#editMessage", $("editDialog")).textContent = e.message; }
  finally { setButtonBusy(btn, false); }
});

// ── Propriétés ──
function openProperties(f, trigger) {
  const list = $("propertiesList");
  list.replaceChildren();
  const row = (k, v) => {
    const dt = document.createElement("dt"); dt.textContent = k;
    const dd = document.createElement("dd"); dd.textContent = v ?? "—";
    list.append(dt, dd);
  };
  row("Nom", f.name);
  row("Chemin", `/${f.path}`);
  row("Type", fileTypeLabel(f));
  row("Taille", f.is_dir ? (f.size || "—") : fmtSize(f.size_bytes));
  row("Modifié", f.modified ? fmtDate(f.modified) : "—");
  if (f.created) row("Créé", fmtDate(f.created));
  openDialog($("propertiesDialog"), trigger);
  api(au(`/files/properties?path=${encodeURIComponent(f.path)}`))
    .then(d => {
      row("Taille (dossier)", d.size || "—");
      if (d.is_dir) row("Éléments", String(d.file_count));
      if (d.mime) row("Type MIME", d.mime);
      if (d.permissions) row("Permissions", d.permissions);
      if (d.owner != null) row("Owner / Groupe", `${d.owner} / ${d.group}`);
    })
    .catch(() => {});
}
$("closePropertiesBtn").addEventListener("click", () => $("propertiesDialog").close());

// ── Corbeille ──
async function loadTrash() {
  try {
    const d = await api(au("/files/trash"));
    S.trash = d.items || [];
    renderTrash();
  } catch (e) { showError(e); }
}
function renderTrash() {
  const b = $("trashBody");
  if (!S.trash.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td"); cell.colSpan = 5; cell.className = "table-empty-cell";
    cell.textContent = "La corbeille est vide.";
    row.append(cell);
    b.replaceChildren(row);
    return;
  }
  b.replaceChildren(...S.trash.map(t => {
    const tr = document.createElement("tr");
    const name = document.createElement("td"); name.textContent = t.name || "";
    const orig = document.createElement("td"); orig.className = "trash-origin"; orig.textContent = `/${t.original_path}`; orig.title = t.original_path;
    const size = document.createElement("td"); size.className = "size-cell"; size.textContent = t.size_bytes ? fmtSize(t.size_bytes) : (t.is_dir ? "Dossier" : "—");
    const date = document.createElement("td"); date.className = "date-cell"; date.textContent = fmtDate(t.deleted_at);
    const acts = document.createElement("td"); acts.className = "action-cell";
    const restore = document.createElement("button"); restore.className = "action-btn"; restore.setAttribute("aria-label", "Restaurer");
    restore.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 10h8m-4-4 4 4-4 4"/></svg>';
    restore.addEventListener("click", async () => {
      try {
        const fd = new FormData(); fd.append("path", t.trashed_rel);
        await api(au("/files/trash/restore"), { method: "POST", body: fd });
        toast("Élément restauré.");
        loadTrash();
      } catch (e) { toast(e.message); }
    });
    acts.append(restore);
    tr.append(name, orig, size, date, acts);
    return tr;
  }));
}
$("btnEmptyTrash").addEventListener("click", async () => {
  if (!S.trash.length) { toast("La corbeille est déjà vide."); return; }
  setButtonBusy($("btnEmptyTrash"), true);
  try {
    const r = await api(au("/files/trash/empty"), { method: "POST" });
    toast(`Corbeille vidée (${r.removed} élément${r.removed > 1 ? "s" : ""}).`);
    loadTrash();
  } catch (e) { toast(e.message); }
  finally { setButtonBusy($("btnEmptyTrash"), false); }
});

// ── Chemin éditable ──
function enablePathInput() {
  const input = $("pathInput");
  input.value = S.path;
  input.hidden = false;
  $("breadcrumb").hidden = true;
  input.focus();
  input.select();
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      const p = input.value.trim().replace(/^\/+/, "");
      input.hidden = true;
      $("breadcrumb").hidden = false;
      navigate(p);
    } else if (e.key === "Escape") {
      input.hidden = true;
      $("breadcrumb").hidden = false;
    }
  });
  input.addEventListener("blur", () => { input.hidden = true; $("breadcrumb").hidden = false; });
}
function renderBreadcrumb() {
  const parts = S.path ? S.path.replace(/\\/g, "/").split("/").filter(Boolean) : [];
  const el = $("breadcrumb"); el.replaceChildren();
  el.hidden = false;
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
  el.addEventListener("click", (e) => {
    if (e.target === el || e.target.classList.contains("sep")) enablePathInput();
  });
  qs("#sidebarPath").textContent = `/mnt/ultra-media/${S.path ? S.path + "/" : ""}`;
}

// ── Vues liste / grille, fichiers cachés ──
$("btnViewToggle").addEventListener("click", () => {
  S.viewMode = S.viewMode === "grid" ? "list" : "grid";
  updateUrl();
  renderFiles();
});
$("btnHiddenToggle").addEventListener("click", () => {
  S.showHidden = !S.showHidden;
  renderFiles();
});

// ── Auto-refresh ──
function startAutoRefresh() {
  stopAutoRefresh();
  S.autoRefreshTimer = setInterval(() => {
    if (document.hidden || S.view !== "files" || S.loading) return;
    if (document.querySelector("dialog[open]")) return;
    const active = document.activeElement;
    if (active && (active.id === "pathInput" || active.id === "searchInput")) return;
    loadFiles();
  }, 30000);
}
function stopAutoRefresh() { if (S.autoRefreshTimer) { clearInterval(S.autoRefreshTimer); S.autoRefreshTimer = null; } }
document.addEventListener("visibilitychange", () => {
  if (document.hidden) stopAutoRefresh(); else startAutoRefresh();
});

// ── Retry ──
$("retryButton").addEventListener("click", () => { clearError(); loadFiles(); });

// ── Init ──
async function init() {
  const u = new URL(window.location.href);
  const v = u.searchParams.get("view") || "files";
  S.path = u.searchParams.get("path") || "";
  S.search = u.searchParams.get("search") || "";
  S.sortKey = ["name", "size", "date", "type", "created"].includes(u.searchParams.get("sort")) ? u.searchParams.get("sort") : "name";
  S.sortDir = u.searchParams.get("direction") === "desc" ? "desc" : "asc";
  S.page = Math.max(1, Number.parseInt(u.searchParams.get("page") || "1", 10) || 1);
  if (u.searchParams.get("viewmode") === "grid") S.viewMode = "grid";
  S.globalSearch = Boolean(u.searchParams.get("global"));
  $("searchInput").value = S.search;
  $("globalSearch").checked = S.globalSearch;
  renderSortHeaders();
  await refreshSession();
  await loadFavs();
  switchView(v);
}
init();
