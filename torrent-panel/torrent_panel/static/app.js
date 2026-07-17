const STORAGE_KEY = "torrent-panel-preferences";
const DEFAULT_PREFS = {
  search: "",
  status: "all",
  category: "all",
  tag: "all",
  sort: "default",
  direction: "asc",
  autoRefresh: true,
  refreshIntervalMs: 6000,
};

const state = {
  csrfToken: "",
  torrents: [],
  selected: new Set(),
  rowErrors: new Map(),
  rowBusy: new Map(),
  pendingDelete: null,
  detailHash: "",
  lastFocus: null,
  refreshTimer: null,
  refreshPromise: null,
  lastSignature: "",
  lastUpdatedAt: null,
  globalActionCount: 0,
  prefs: loadPrefs(),
};

const els = {
  rows: document.querySelector("#torrentRows"),
  summary: document.querySelector("#summary"),
  summaryGrid: document.querySelector("#summaryGrid"),
  alert: document.querySelector("#alert"),
  alertText: document.querySelector("#alertText"),
  retryButton: document.querySelector("#retryButton"),
  empty: document.querySelector("#emptyState"),
  refreshStatus: document.querySelector("#refreshStatus"),
  refreshButton: document.querySelector("#refreshButton"),
  autoRefreshToggle: document.querySelector("#autoRefreshToggle"),
  refreshInterval: document.querySelector("#refreshInterval"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  categoryFilter: document.querySelector("#categoryFilter"),
  tagFilter: document.querySelector("#tagFilter"),
  sortSelect: document.querySelector("#sortSelect"),
  resetFilters: document.querySelector("#resetFilters"),
  selectVisible: document.querySelector("#selectVisible"),
  bulkBar: document.querySelector("#bulkBar"),
  bulkCount: document.querySelector("#bulkCount"),
  bulkPause: document.querySelector("#bulkPause"),
  bulkResume: document.querySelector("#bulkResume"),
  bulkDelete: document.querySelector("#bulkDelete"),
  addForm: document.querySelector("#addForm"),
  addButton: document.querySelector("#addButton"),
  magnetInput: document.querySelector("#magnetInput"),
  categoryInput: document.querySelector("#categoryInput"),
  tagsInput: document.querySelector("#tagsInput"),
  pausedInput: document.querySelector("#pausedInput"),
  magnetMessage: document.querySelector("#magnetMessage"),
  toast: document.querySelector("#toast"),
  deleteDialog: document.querySelector("#deleteDialog"),
  deleteForm: document.querySelector("#deleteForm"),
  deleteTitle: document.querySelector("#deleteTitle"),
  deleteTorrentName: document.querySelector("#deleteTorrentName"),
  strongConfirm: document.querySelector("#strongConfirm"),
  confirmText: document.querySelector("#confirmText"),
  cancelDeleteButton: document.querySelector("#cancelDeleteButton"),
  confirmDeleteButton: document.querySelector("#confirmDeleteButton"),
  detailDialog: document.querySelector("#detailDialog"),
  detailTitle: document.querySelector("#detailTitle"),
  detailBody: document.querySelector("#detailBody"),
};

const STATUS_GROUPS = {
  all: "Tous les états",
  error: "Erreurs et blocages",
  downloading: "Téléchargement",
  waiting: "En attente",
  checking: "Vérification",
  sharing: "Partage",
  complete: "Terminés",
  paused: "En pause",
};

const STATE_META = {
  downloading: { group: "downloading", text: "Téléchargement", icon: "↓" },
  forcedDL: { group: "downloading", text: "Téléchargement forcé", icon: "↓" },
  metaDL: { group: "downloading", text: "Métadonnées", icon: "↓" },
  uploading: { group: "sharing", text: "Partage", icon: "↑" },
  forcedUP: { group: "sharing", text: "Partage forcé", icon: "↑" },
  queuedDL: { group: "waiting", text: "En attente", icon: "…" },
  queuedUP: { group: "waiting", text: "En attente de partage", icon: "…" },
  checkingDL: { group: "checking", text: "Vérification", icon: "✓" },
  checkingUP: { group: "checking", text: "Vérification", icon: "✓" },
  checkingResumeData: { group: "checking", text: "Vérification reprise", icon: "✓" },
  pausedDL: { group: "paused", text: "En pause", icon: "Ⅱ" },
  pausedUP: { group: "paused", text: "Terminé en pause", icon: "Ⅱ" },
  stoppedDL: { group: "paused", text: "Arrêté", icon: "Ⅱ" },
  stoppedUP: { group: "paused", text: "Terminé arrêté", icon: "Ⅱ" },
  stalledDL: { group: "error", text: "Bloqué", icon: "!" },
  stalledUP: { group: "error", text: "Partage bloqué", icon: "!" },
  error: { group: "error", text: "Erreur", icon: "!" },
  missingFiles: { group: "error", text: "Fichiers manquants", icon: "!" },
  moving: { group: "waiting", text: "Déplacement", icon: "…" },
  unknown: { group: "waiting", text: "État inconnu", icon: "…" },
};

const SORT_LABELS = {
  default: "Priorité opérationnelle",
  name: "Nom",
  state: "État",
  progress: "Progression",
  downloadSpeed: "Vitesse descendante",
  uploadSpeed: "Vitesse montante",
  ratio: "Ratio",
  size: "Taille",
  eta: "ETA",
  addedOn: "Date d'ajout",
};

const GROUP_ORDER = {
  error: 0,
  downloading: 1,
  waiting: 2,
  checking: 3,
  sharing: 4,
  complete: 5,
  paused: 6,
};

function loadPrefs() {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

function savePrefs() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.prefs));
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatSpeed(value) {
  return `${formatBytes(value)}/s`;
}

function formatRatio(value) {
  return (Number(value) || 0).toFixed(2);
}

function formatPercent(value) {
  return `${(Math.max(0, Math.min(1, Number(value) || 0)) * 100).toFixed(1)} %`;
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0 || value >= 8640000) return "—";
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours >= 24) return `${Math.floor(hours / 24)} j ${hours % 24} h`;
  if (hours > 0) return `${hours} h ${minutes.toString().padStart(2, "0")}`;
  return `${Math.max(1, minutes)} min`;
}

function formatDate(timestamp) {
  const value = Number(timestamp);
  if (!Number.isFinite(value) || value <= 0) return "—";
  return new Date(value * 1000).toLocaleString("fr-FR", {
    dateStyle: "short",
    timeStyle: "short",
  });
}

function normalizeTags(tags) {
  return String(tags || "")
    .split(",")
    .map((tag) => tag.trim())
    .filter(Boolean);
}

function stateMeta(torrent) {
  const raw = String(torrent.state || "unknown");
  const meta = STATE_META[raw] || STATE_META.unknown;
  if (meta.group === "paused" && Number(torrent.progress) >= 1) {
    return { ...meta, group: "paused", text: meta.text };
  }
  if (!STATE_META[raw] && Number(torrent.progress) >= 1) {
    return { group: "complete", text: "Terminé", icon: "✓" };
  }
  return meta;
}

function isComplete(torrent) {
  return Number(torrent.progress) >= 1;
}

function describeError(error) {
  const message = error?.message || "Action impossible pour le moment.";
  const recovery = error?.recovery || "Réessayer";
  return `${message} ${recovery}.`;
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

function showError(error) {
  els.alertText.textContent = describeError(error);
  els.alert.hidden = false;
}

function clearError() {
  els.alert.hidden = true;
  els.alertText.textContent = "";
}

async function api(path, options = {}, retryCsrf = true) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if ((options.method || "GET").toUpperCase() !== "GET") {
    headers.set("X-Torrent-Panel-CSRF", state.csrfToken);
  }

  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (response.ok) return payload;

  const detail = typeof payload.detail === "object" && payload.detail ? payload.detail : {};
  const error = new Error(detail.message || payload.detail || "Action impossible pour le moment.");
  error.code = detail.code || `http_${response.status}`;
  error.recovery = detail.recovery || (response.status === 429 ? "Réessayer" : "Réessayer");
  error.status = response.status;

  if (response.status === 403 && error.code === "csrf_expired" && retryCsrf) {
    await refreshSession();
    return api(path, options, false);
  }
  throw error;
}

async function refreshSession() {
  const session = await api("api/session", {}, false);
  state.csrfToken = session.csrfToken;
}

function summarize(torrents) {
  const result = {
    total: torrents.length,
    downloading: 0,
    sharing: 0,
    complete: 0,
    paused: 0,
    error: 0,
    downSpeed: 0,
    upSpeed: 0,
    remaining: 0,
  };
  for (const torrent of torrents) {
    const meta = stateMeta(torrent);
    if (meta.group === "downloading") result.downloading += 1;
    if (meta.group === "sharing") result.sharing += 1;
    if (isComplete(torrent)) result.complete += 1;
    if (meta.group === "paused") result.paused += 1;
    if (meta.group === "error") result.error += 1;
    result.downSpeed += Number(torrent.downloadSpeed) || 0;
    result.upSpeed += Number(torrent.uploadSpeed) || 0;
    result.remaining += Number(torrent.remaining) || Math.max(0, (Number(torrent.size) || 0) - (Number(torrent.downloaded) || 0));
  }
  return result;
}

function renderSummary() {
  const data = summarize(state.torrents);
  const cards = [
    ["Total", data.total],
    ["Actifs", data.downloading],
    ["Partage", data.sharing],
    ["Terminés", data.complete],
    ["Pause", data.paused],
    ["Erreur/bloqué", data.error],
    ["Descendant", formatSpeed(data.downSpeed)],
    ["Montant", formatSpeed(data.upSpeed)],
    ["Restant", formatBytes(data.remaining)],
  ];
  els.summaryGrid.replaceChildren(
    ...cards.map(([label, value]) => {
      const item = document.createElement("div");
      item.className = "stat";
      const strong = document.createElement("strong");
      strong.textContent = value;
      const span = document.createElement("span");
      span.textContent = label;
      item.append(strong, span);
      return item;
    }),
  );
}

function uniqueValues(getter) {
  return [...new Set(state.torrents.flatMap(getter).filter(Boolean))].sort((a, b) => a.localeCompare(b, "fr"));
}

function fillSelect(select, entries, value) {
  const current = value || select.value;
  select.replaceChildren(
    ...entries.map(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      return option;
    }),
  );
  select.value = entries.some(([optionValue]) => optionValue === current) ? current : "all";
}

function renderControls() {
  els.searchInput.value = state.prefs.search;
  els.autoRefreshToggle.checked = state.prefs.autoRefresh;
  els.refreshInterval.value = String(state.prefs.refreshIntervalMs);
  fillSelect(els.statusFilter, Object.entries(STATUS_GROUPS), state.prefs.status);
  fillSelect(els.categoryFilter, [["all", "Toutes"], ...uniqueValues((t) => [String(t.category || "").trim()]).map((v) => [v, v])], state.prefs.category);
  fillSelect(els.tagFilter, [["all", "Tous"], ...uniqueValues((t) => normalizeTags(t.tags)).map((v) => [v, v])], state.prefs.tag);
  els.sortSelect.replaceChildren(
    ...Object.entries(SORT_LABELS).map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
  els.sortSelect.value = state.prefs.sort;
}

function filteredTorrents() {
  const search = state.prefs.search.trim().toLocaleLowerCase("fr");
  const filtered = state.torrents.filter((torrent) => {
    const meta = stateMeta(torrent);
    if (search && !String(torrent.name || "").toLocaleLowerCase("fr").includes(search)) return false;
    if (state.prefs.status === "complete" && !isComplete(torrent)) return false;
    if (state.prefs.status !== "all" && state.prefs.status !== "complete" && meta.group !== state.prefs.status) return false;
    if (state.prefs.category !== "all" && String(torrent.category || "") !== state.prefs.category) return false;
    if (state.prefs.tag !== "all" && !normalizeTags(torrent.tags).includes(state.prefs.tag)) return false;
    return true;
  });
  return sortTorrents(filtered);
}

function sortValue(torrent, key) {
  if (key === "state") return GROUP_ORDER[stateMeta(torrent).group] ?? 99;
  if (key === "name") return String(torrent.name || "").toLocaleLowerCase("fr");
  return Number(torrent[key]) || 0;
}

function sortTorrents(torrents) {
  const key = state.prefs.sort;
  const direction = state.prefs.direction === "desc" ? -1 : 1;
  return [...torrents].sort((a, b) => {
    if (key === "default") {
      const groupDiff = (GROUP_ORDER[stateMeta(a).group] ?? 99) - (GROUP_ORDER[stateMeta(b).group] ?? 99);
      if (groupDiff) return groupDiff;
      return (Number(b.downloadSpeed) || 0) - (Number(a.downloadSpeed) || 0);
    }
    const left = sortValue(a, key);
    const right = sortValue(b, key);
    if (typeof left === "string" || typeof right === "string") {
      return String(left).localeCompare(String(right), "fr") * direction;
    }
    return (left - right) * direction;
  });
}

function setSort(key) {
  if (state.prefs.sort === key) {
    state.prefs.direction = state.prefs.direction === "asc" ? "desc" : "asc";
  } else {
    state.prefs.sort = key;
    state.prefs.direction = key === "name" ? "asc" : "desc";
  }
  savePrefs();
  render();
}

function button(label, className, onClick) {
  const el = document.createElement("button");
  el.type = "button";
  el.className = `button ${className}`;
  el.textContent = label;
  el.addEventListener("click", onClick);
  return el;
}

function renderBadge(meta) {
  const badge = document.createElement("span");
  badge.className = `badge ${meta.group}`;
  const icon = document.createElement("span");
  icon.className = "badge-icon";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = meta.icon;
  const text = document.createElement("span");
  text.textContent = meta.text;
  badge.append(icon, text);
  return badge;
}

function renderProgress(torrent) {
  const progress = Math.max(0, Math.min(100, (Number(torrent.progress) || 0) * 100));
  const wrap = document.createElement("div");
  wrap.className = "progress-wrap";
  const track = document.createElement("div");
  track.className = "progress-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", "100");
  track.setAttribute("aria-valuenow", progress.toFixed(1));
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  bar.style.width = `${progress}%`;
  const text = document.createElement("div");
  text.className = "progress-text";
  text.textContent = `${progress.toFixed(1)} %`;
  track.append(bar);
  wrap.append(track, text);
  return wrap;
}

function renderRow(torrent) {
  const meta = stateMeta(torrent);
  const hash = torrent.hash;
  const busyText = state.rowBusy.get(hash);
  const tr = document.createElement("tr");
  tr.dataset.hash = hash;
  if (busyText) tr.classList.add("is-busy");

  const selectTd = document.createElement("td");
  selectTd.dataset.label = "Sélection";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = state.selected.has(hash);
  checkbox.disabled = Boolean(busyText);
  checkbox.setAttribute("aria-label", `Sélectionner ${torrent.name}`);
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) state.selected.add(hash);
    else state.selected.delete(hash);
    renderSelection();
  });
  selectTd.append(checkbox);

  const nameTd = document.createElement("td");
  nameTd.dataset.label = "Nom";
  const nameButton = document.createElement("button");
  nameButton.type = "button";
  nameButton.className = "name-button";
  nameButton.title = torrent.name;
  nameButton.textContent = torrent.name;
  nameButton.addEventListener("click", (event) => openDetails(torrent.hash, event.currentTarget));
  nameTd.append(nameButton);

  const stateTd = document.createElement("td");
  stateTd.dataset.label = "État";
  stateTd.append(renderBadge(meta));

  const progressTd = document.createElement("td");
  progressTd.dataset.label = "Progression";
  progressTd.className = "progress-cell";
  progressTd.append(renderProgress(torrent));

  const downTd = textCell("Téléchargement", formatSpeed(torrent.downloadSpeed), "mono");
  const upTd = textCell("Envoi", formatSpeed(torrent.uploadSpeed), "mono");
  const ratioTd = textCell("Ratio", formatRatio(torrent.ratio), "mono");
  const sizeTd = textCell("Taille", formatBytes(torrent.size), "mono");
  const etaTd = textCell("ETA", formatEta(torrent.eta), "mono");

  const actionTd = document.createElement("td");
  actionTd.dataset.label = "Actions";
  const actions = document.createElement("div");
  actions.className = "action-row";
  const isPaused = meta.group === "paused";
  const pauseOrResume = button(isPaused ? "Reprendre" : "Pause", isPaused ? "primary" : "secondary", () => {
    runTorrentAction([hash], isPaused ? "resume" : "pause");
  });
  const detail = button("Détails", "secondary", (event) => openDetails(hash, event.currentTarget));
  const remove = button("Supprimer", "danger", (event) => openDeleteDialog([torrent], event.currentTarget));
  for (const control of [pauseOrResume, detail, remove]) {
    control.disabled = Boolean(busyText);
  }
  actions.append(pauseOrResume, detail, remove);
  const inline = document.createElement("div");
  inline.className = "row-status";
  inline.setAttribute("aria-live", "polite");
  inline.textContent = busyText || state.rowErrors.get(hash) || "";
  actionTd.append(actions, inline);

  tr.append(selectTd, nameTd, stateTd, progressTd, downTd, upTd, ratioTd, sizeTd, etaTd, actionTd);
  return tr;
}

function textCell(label, value, className = "") {
  const td = document.createElement("td");
  td.dataset.label = label;
  td.className = className;
  td.textContent = value;
  return td;
}

function renderSelection(visible = filteredTorrents()) {
  for (const hash of [...state.selected]) {
    if (!state.torrents.some((torrent) => torrent.hash === hash)) state.selected.delete(hash);
  }
  const visibleHashes = visible.map((torrent) => torrent.hash);
  const selectedVisible = visibleHashes.filter((hash) => state.selected.has(hash));
  els.selectVisible.checked = visibleHashes.length > 0 && selectedVisible.length === visibleHashes.length;
  els.selectVisible.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleHashes.length;
  els.bulkBar.hidden = state.selected.size === 0;
  els.bulkCount.textContent = `${state.selected.size} sélectionné${state.selected.size > 1 ? "s" : ""}`;
}

function renderSortHeaders() {
  document.querySelectorAll(".sort-head").forEach((head) => {
    const key = head.dataset.sort;
    const active = key === state.prefs.sort;
    head.setAttribute("aria-sort", active ? (state.prefs.direction === "asc" ? "ascending" : "descending") : "none");
    head.textContent = `${head.textContent.replace(/\s+[↑↓]$/, "")}${active ? (state.prefs.direction === "asc" ? " ↑" : " ↓") : ""}`;
  });
}

function render() {
  renderSummary();
  renderControls();
  const visible = filteredTorrents();
  els.rows.replaceChildren(...visible.map(renderRow));
  els.empty.textContent = state.torrents.length === 0 ? "Aucun torrent pour le moment." : "Aucun torrent ne correspond aux filtres.";
  els.empty.hidden = visible.length !== 0;
  els.summary.textContent = `${visible.length} affiché${visible.length > 1 ? "s" : ""} sur ${state.torrents.length}`;
  renderSelection(visible);
  renderSortHeaders();
  updateDetails();
}

async function loadTorrents({ silent = false, force = false } = {}) {
  if (state.refreshPromise) return state.refreshPromise;
  if (!force && (state.globalActionCount > 0 || document.hidden)) return null;
  if (!silent) els.refreshStatus.textContent = "Actualisation...";

  state.refreshPromise = (async () => {
    try {
      const payload = await api("api/torrents");
      const torrents = Array.isArray(payload.torrents) ? payload.torrents : [];
      const signature = JSON.stringify(torrents);
      state.lastUpdatedAt = new Date();
      if (signature !== state.lastSignature) {
        state.torrents = torrents;
        state.lastSignature = signature;
        render();
      } else {
        renderSelection();
      }
      clearError();
      els.refreshStatus.textContent = `À jour ${state.lastUpdatedAt.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" })}`;
    } catch (error) {
      showError(error);
      els.refreshStatus.textContent = state.lastUpdatedAt
        ? `Dernier succès ${state.lastUpdatedAt.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`
        : "Erreur";
    } finally {
      state.refreshPromise = null;
    }
  })();
  return state.refreshPromise;
}

function actionText(action) {
  return {
    pause: "Mise en pause…",
    resume: "Reprise…",
    delete: "Suppression…",
    add: "Ajout…",
  }[action] || "Action…";
}

async function runTorrentAction(hashes, action, options = {}) {
  const busy = actionText(action);
  hashes.forEach((hash) => {
    state.rowBusy.set(hash, busy);
    state.rowErrors.delete(hash);
  });
  state.globalActionCount += 1;
  render();
  try {
    await api(`api/torrents/${action}`, {
      method: "POST",
      body: JSON.stringify({ hashes, ...options }),
    });
    showToast(action === "pause" ? "Torrent mis en pause." : action === "resume" ? "Torrent repris." : "Torrent supprimé.");
    hashes.forEach((hash) => state.selected.delete(hash));
    await loadTorrents({ silent: true, force: true });
  } catch (error) {
    hashes.forEach((hash) => state.rowErrors.set(hash, describeError(error)));
  } finally {
    hashes.forEach((hash) => state.rowBusy.delete(hash));
    state.globalActionCount -= 1;
    render();
  }
}

function openDeleteDialog(torrents, trigger) {
  if (!torrents.length) {
    showToast("Aucun torrent sélectionné.");
    return;
  }
  state.lastFocus = trigger || document.activeElement;
  state.pendingDelete = torrents;
  els.deleteTitle.textContent = torrents.length > 1 ? "Supprimer les torrents" : "Supprimer le torrent";
  els.deleteTorrentName.textContent = torrents.length > 1
    ? `${torrents.length} torrents sélectionnés`
    : torrents[0]?.name || "";
  els.deleteForm.deleteMode.value = "torrent";
  els.confirmText.value = "";
  updateDeleteConfirm();
  els.deleteDialog.showModal();
}

function updateDeleteConfirm() {
  const withFiles = els.deleteForm.deleteMode.value === "files";
  els.strongConfirm.hidden = !withFiles;
  els.confirmDeleteButton.disabled = withFiles && els.confirmText.value.trim() !== "SUPPRIMER";
}

async function confirmDelete() {
  const torrents = state.pendingDelete || [];
  if (!torrents.length) return;
  const deleteFiles = els.deleteForm.deleteMode.value === "files";
  if (deleteFiles && els.confirmText.value.trim() !== "SUPPRIMER") {
    els.confirmText.focus();
    return;
  }
  els.confirmDeleteButton.disabled = true;
  els.cancelDeleteButton.disabled = true;
  els.confirmDeleteButton.textContent = "Suppression…";
  els.deleteDialog.close();
  try {
    await runTorrentAction(torrents.map((torrent) => torrent.hash), "delete", { deleteFiles });
    state.pendingDelete = null;
  } finally {
    els.confirmDeleteButton.disabled = false;
    els.cancelDeleteButton.disabled = false;
    els.confirmDeleteButton.textContent = "Supprimer";
  }
}

function detailRows(torrent) {
  const meta = stateMeta(torrent);
  return [
    ["Nom complet", torrent.name],
    ["Hash", torrent.hash],
    ["État", meta.text],
    ["Progression", formatPercent(torrent.progress)],
    ["Téléchargé", formatBytes(torrent.downloaded)],
    ["Restant", formatBytes(torrent.remaining)],
    ["Descendant", formatSpeed(torrent.downloadSpeed)],
    ["Montant", formatSpeed(torrent.uploadSpeed)],
    ["ETA", formatEta(torrent.eta)],
    ["Ratio", formatRatio(torrent.ratio)],
    ["Seeders", Number(torrent.seeders) || 0],
    ["Leechers", Number(torrent.leechers) || 0],
    ["Disponibilité", formatRatio(torrent.availability)],
    ["Catégorie", torrent.category || "—"],
    ["Tags", torrent.tags || "—"],
    ["Tracker", torrent.tracker || "—"],
    ["Chemin", torrent.savePath || "—"],
    ["Ajout", formatDate(torrent.addedOn)],
    ["Fin", formatDate(torrent.completionOn)],
  ];
}

function openDetails(hash, trigger) {
  state.detailHash = hash;
  state.lastFocus = trigger || document.activeElement;
  updateDetails();
  els.detailDialog.showModal();
}

function updateDetails() {
  if (!state.detailHash) return;
  const torrent = state.torrents.find((item) => item.hash === state.detailHash);
  if (!torrent) {
    els.detailDialog.close();
    return;
  }
  els.detailTitle.textContent = torrent.name;
  const dl = document.createElement("dl");
  for (const [label, value] of detailRows(torrent)) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    if (label === "Hash") {
      const code = document.createElement("code");
      code.textContent = value;
      const copy = button("Copier", "secondary", async () => {
        await navigator.clipboard?.writeText(value);
        showToast("Hash copié.");
      });
      dd.append(code, copy);
    } else {
      dd.textContent = value;
    }
    dl.append(dt, dd);
  }
  els.detailBody.replaceChildren(dl);
}

async function submitMagnets(event) {
  event.preventDefault();
  els.magnetMessage.textContent = "";
  const magnets = els.magnetInput.value.splitlines?.() || els.magnetInput.value.split(/\r?\n/);
  const valid = magnets.map((item) => item.trim()).filter(Boolean);
  if (!valid.length) {
    els.magnetMessage.textContent = "Ajoutez au moins un lien magnet.";
    els.magnetInput.focus();
    return;
  }
  els.addButton.disabled = true;
  els.addButton.textContent = "Ajout…";
  state.globalActionCount += 1;
  try {
    const payload = await api("api/torrents/add", {
      method: "POST",
      body: JSON.stringify({
        magnets: valid,
        category: els.categoryInput.value.trim(),
        tags: els.tagsInput.value.trim(),
        paused: els.pausedInput.checked,
      }),
    });
    const rejected = Array.isArray(payload.rejected) ? payload.rejected : [];
    els.magnetMessage.textContent = `${payload.accepted || 0} accepté${payload.accepted > 1 ? "s" : ""}, ${rejected.length} refusé${rejected.length > 1 ? "s" : ""}.`;
    if (payload.accepted) {
      els.magnetInput.value = "";
      showToast("Magnet envoyé à qBittorrent.");
      await loadTorrents({ silent: true, force: true });
    }
  } catch (error) {
    els.magnetMessage.textContent = describeError(error);
    els.magnetInput.focus();
  } finally {
    state.globalActionCount -= 1;
    els.addButton.disabled = false;
    els.addButton.textContent = "Ajouter";
  }
}

function restartRefreshTimer() {
  window.clearInterval(state.refreshTimer);
  state.refreshTimer = null;
  if (!state.prefs.autoRefresh) return;
  state.refreshTimer = window.setInterval(() => {
    loadTorrents({ silent: true });
  }, Number(state.prefs.refreshIntervalMs) || 6000);
}

function updatePreference(key, value) {
  state.prefs[key] = value;
  savePrefs();
  render();
}

function bindEvents() {
  els.refreshButton.addEventListener("click", () => loadTorrents({ force: true }));
  els.retryButton.addEventListener("click", () => loadTorrents({ force: true }));
  els.searchInput.addEventListener("input", () => updatePreference("search", els.searchInput.value));
  els.statusFilter.addEventListener("change", () => updatePreference("status", els.statusFilter.value));
  els.categoryFilter.addEventListener("change", () => updatePreference("category", els.categoryFilter.value));
  els.tagFilter.addEventListener("change", () => updatePreference("tag", els.tagFilter.value));
  els.sortSelect.addEventListener("change", () => updatePreference("sort", els.sortSelect.value));
  els.resetFilters.addEventListener("click", () => {
    state.prefs = { ...state.prefs, search: "", status: "all", category: "all", tag: "all", sort: "default", direction: "asc" };
    savePrefs();
    render();
  });
  els.autoRefreshToggle.addEventListener("change", () => {
    state.prefs.autoRefresh = els.autoRefreshToggle.checked;
    savePrefs();
    restartRefreshTimer();
    renderControls();
  });
  els.refreshInterval.addEventListener("change", () => {
    state.prefs.refreshIntervalMs = Number(els.refreshInterval.value);
    savePrefs();
    restartRefreshTimer();
  });
  document.querySelectorAll(".sort-head").forEach((head) => {
    head.addEventListener("click", () => setSort(head.dataset.sort));
  });
  els.selectVisible.addEventListener("change", () => {
    const visible = filteredTorrents();
    if (els.selectVisible.checked) {
      visible.forEach((torrent) => state.selected.add(torrent.hash));
    } else {
      visible.forEach((torrent) => state.selected.delete(torrent.hash));
    }
    render();
  });
  els.bulkPause.addEventListener("click", () => runTorrentAction([...state.selected], "pause"));
  els.bulkResume.addEventListener("click", () => runTorrentAction([...state.selected], "resume"));
  els.bulkDelete.addEventListener("click", (event) => {
    const selectedTorrents = state.torrents.filter((torrent) => state.selected.has(torrent.hash));
    openDeleteDialog(selectedTorrents, event.currentTarget);
  });
  els.addForm.addEventListener("submit", submitMagnets);
  els.deleteForm.addEventListener("change", updateDeleteConfirm);
  els.confirmText.addEventListener("input", updateDeleteConfirm);
  els.cancelDeleteButton.addEventListener("click", () => {
    state.pendingDelete = null;
    els.deleteDialog.close();
  });
  els.confirmDeleteButton.addEventListener("click", () => {
    confirmDelete();
  });
  els.deleteForm.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmDelete();
  });
  els.deleteDialog.addEventListener("close", () => state.lastFocus?.focus?.());
  els.detailDialog.addEventListener("close", () => {
    state.detailHash = "";
    state.lastFocus?.focus?.();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadTorrents({ silent: true, force: true });
  });
}

async function init() {
  bindEvents();
  renderControls();
  restartRefreshTimer();
  try {
    await refreshSession();
    await loadTorrents({ force: true });
  } catch (error) {
    showError(error);
    els.refreshStatus.textContent = "Erreur";
  }
}

init();
