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
  lastCategory: "",
  lastTags: "",
};

const state = {
  csrfToken: "",
  activeView: "home",
  torrents: [],
  dashboard: { alerts: [], criticalCount: 0, quickActions: [], services: [], mediaAutomation: { enabled: false, entries: [], notification: null } },
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
  homeView: document.querySelector("#homeView"),
  torrentsView: document.querySelector("#torrentsView"),
  homeSummary: document.querySelector("#homeSummary"),
  criticalAlerts: document.querySelector("#criticalAlerts"),
  quickActions: document.querySelector("#quickActions"),
  alertsList: document.querySelector("#alertsList"),
  alertsSummary: document.querySelector("#alertsSummary"),
  servicesGrid: document.querySelector("#servicesGrid"),
  servicesSummary: document.querySelector("#servicesSummary"),
  mediaAutomationSummary: document.querySelector("#mediaAutomationSummary"),
  mediaAutomationNotice: document.querySelector("#mediaAutomationNotice"),
  mediaAutomationList: document.querySelector("#mediaAutomationList"),
  homeNavLink: document.querySelector("#homeNavLink"),
  torrentsNavLink: document.querySelector("#torrentsNavLink"),
  navAlertCount: document.querySelector("#navAlertCount"),
  rows: document.querySelector("#torrentRows"),
  summary: document.querySelector("#summary"),
  summaryGrid: document.querySelector("#summaryGrid"),
  alert: document.querySelector("#alert"),
  alertText: document.querySelector("#alertText"),
  retryButton: document.querySelector("#retryButton"),
  empty: document.querySelector("#emptyState"),
  refreshStatus: document.querySelector("#refreshStatus"),
  refreshButton: document.querySelector("#refreshButton"),
  openAddPanelButton: document.querySelector("#openAddPanelButton"),
  autoRefreshToggle: document.querySelector("#autoRefreshToggle"),
  refreshInterval: document.querySelector("#refreshInterval"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  categoryFilter: document.querySelector("#categoryFilter"),
  tagFilter: document.querySelector("#tagFilter"),
  sortSelect: document.querySelector("#sortSelect"),
  resetFilters: document.querySelector("#resetFilters"),
  clearActiveFilters: document.querySelector("#clearActiveFilters"),
  quickFilters: document.querySelector("#quickFilters"),
  filterNotice: document.querySelector("#filterNotice"),
  selectVisible: document.querySelector("#selectVisible"),
  bulkBar: document.querySelector("#bulkBar"),
  bulkCount: document.querySelector("#bulkCount"),
  bulkPause: document.querySelector("#bulkPause"),
  bulkResume: document.querySelector("#bulkResume"),
  bulkForceShare: document.querySelector("#bulkForceShare"),
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
  active: "Actifs",
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

const QUICK_FILTERS = [
  { key: "all", label: "Tous" },
  { key: "active", label: "Actifs" },
  { key: "error", label: "Bloqués" },
  { key: "complete", label: "Terminés" },
  { key: "paused", label: "En pause" },
];

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
  return new Date(value * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function formatIsoDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
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
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if ((options.method || "GET").toUpperCase() !== "GET") headers.set("X-Torrent-Panel-CSRF", state.csrfToken);

  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (response.ok) return payload;

  const detail = typeof payload.detail === "object" && payload.detail ? payload.detail : {};
  const error = new Error(detail.message || payload.detail || "Action impossible pour le moment.");
  error.code = detail.code || `http_${response.status}`;
  error.recovery = detail.recovery || "Réessayer";
  error.status = response.status;
  if (response.status === 403 && error.code === "csrf_expired" && retryCsrf) {
    await refreshSession();
    return api(path, options, false);
  }
  throw error;
}

async function refreshSession() {
  const session = await api("api/session", { cache: "no-store" }, false);
  state.csrfToken = session.csrfToken;
}

function currentUrl() {
  const href = window.location?.href || "http://localhost/torrent-panel/";
  if (typeof URL !== "undefined") return new URL(href);
  const raw = String(href);
  const query = raw.includes("?") ? raw.slice(raw.indexOf("?") + 1) : "";
  const params = new Map(
    query
      .split("&")
      .filter(Boolean)
      .map((entry) => {
        const [key, value = ""] = entry.split("=");
        return [decodeURIComponent(key), decodeURIComponent(value)];
      }),
  );
  return {
    searchParams: {
      get(key) {
        return params.has(key) ? params.get(key) : null;
      },
      set(key, value) {
        params.set(key, String(value));
      },
      delete(key) {
        params.delete(key);
      },
    },
    toString() {
      const base = raw.split("?")[0];
      const search = [...params.entries()].map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`).join("&");
      return search ? `${base}?${search}` : base;
    },
  };
}

function updateUrl(replace = true) {
  const url = currentUrl();
  url.searchParams.set("view", state.activeView);
  if (state.activeView === "torrents") {
    for (const [key, value] of Object.entries({
      search: state.prefs.search,
      status: state.prefs.status,
      category: state.prefs.category,
      tag: state.prefs.tag,
      sort: state.prefs.sort,
      direction: state.prefs.direction,
    })) {
      if (value && value !== DEFAULT_PREFS[key]) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
  } else {
    ["search", "status", "category", "tag", "sort", "direction"].forEach((key) => url.searchParams.delete(key));
  }
  const method = replace ? "replaceState" : "pushState";
  window.history?.[method]?.({}, "", url.toString());
}

function applyUrlState() {
  const url = currentUrl();
  state.activeView = url.searchParams.get("view") === "torrents" ? "torrents" : "home";
  const search = url.searchParams.get("search");
  const status = url.searchParams.get("status");
  const category = url.searchParams.get("category");
  const tag = url.searchParams.get("tag");
  const sort = url.searchParams.get("sort");
  const direction = url.searchParams.get("direction");
  if (search !== null) state.prefs.search = search;
  if (status !== null) state.prefs.status = status;
  if (category !== null) state.prefs.category = category;
  if (tag !== null) state.prefs.tag = tag;
  if (sort !== null) state.prefs.sort = sort;
  if (direction !== null) state.prefs.direction = direction;
}

function setView(view, { push = true } = {}) {
  state.activeView = view === "torrents" ? "torrents" : "home";
  els.homeView.hidden = state.activeView !== "home";
  els.torrentsView.hidden = state.activeView !== "torrents";
  if (els.homeNavLink) els.homeNavLink.setAttribute("aria-current", state.activeView === "home" ? "page" : "false");
  if (els.torrentsNavLink) els.torrentsNavLink.setAttribute("aria-current", state.activeView === "torrents" ? "page" : "false");
  updateUrl(!push);
}

function summarize(torrents) {
  const result = {
    total: torrents.length,
    downloading: 0,
    sharing: 0,
    active: 0,
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
    if (["downloading", "sharing", "waiting", "checking"].includes(meta.group)) result.active += 1;
    if (isComplete(torrent)) result.complete += 1;
    if (meta.group === "paused") result.paused += 1;
    if (meta.group === "error") result.error += 1;
    result.downSpeed += Number(torrent.downloadSpeed) || 0;
    result.upSpeed += Number(torrent.uploadSpeed) || 0;
    result.remaining += Number(torrent.remaining) || Math.max(0, (Number(torrent.size) || 0) - (Number(torrent.downloaded) || 0));
  }
  return result;
}

function matchesQuickFilter(status, quickKey) {
  if (quickKey === "all") return status === "all";
  if (quickKey === "active") return ["downloading", "waiting", "checking", "sharing"].includes(status);
  return status === quickKey;
}

function renderSummary() {
  const data = summarize(state.torrents);
  const cards = [
    ["active", "Actifs", data.active],
    ["error", "Bloqués", data.error],
    [null, "Descendant", formatSpeed(data.downSpeed)],
    [null, "Montant", formatSpeed(data.upSpeed)],
    [null, "Restant", formatBytes(data.remaining)],
  ];
  els.summaryGrid.replaceChildren(
    ...cards.map(([filterKey, label, value]) => {
      const item = document.createElement(filterKey ? "button" : "div");
      item.className = `stat${filterKey ? " stat-button" : ""}`;
      if (filterKey) {
        item.type = "button";
        item.dataset.filter = filterKey;
        item.setAttribute("aria-pressed", String(matchesQuickFilter(state.prefs.status, filterKey)));
      }
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
  select.value = entries.some(([optionValue]) => optionValue === current) ? current : entries[0]?.[0];
}

function renderQuickFilters() {
  els.quickFilters.replaceChildren(
    ...QUICK_FILTERS.map((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `button ${matchesQuickFilter(state.prefs.status, item.key) ? "primary" : "secondary"}`;
      button.dataset.quickFilter = item.key;
      button.textContent = item.label;
      button.setAttribute("aria-pressed", String(matchesQuickFilter(state.prefs.status, item.key)));
      return button;
    }),
  );
}

function activeFilterLabels() {
  const labels = [];
  if (state.prefs.status !== "all") {
    const quick = QUICK_FILTERS.find((item) => matchesQuickFilter(state.prefs.status, item.key));
    labels.push(`État : ${quick?.label || STATUS_GROUPS[state.prefs.status] || state.prefs.status}`);
  }
  if (state.prefs.search.trim()) labels.push(`Recherche : ${state.prefs.search.trim()}`);
  if (state.prefs.category !== "all") labels.push(`Catégorie : ${state.prefs.category}`);
  if (state.prefs.tag !== "all") labels.push(`Tag : ${state.prefs.tag}`);
  return labels;
}

function renderFilterNotice() {
  const labels = activeFilterLabels();
  els.filterNotice.textContent = labels.length ? `Filtres actifs : ${labels.join(" · ")}` : "Aucun filtre actif.";
  els.clearActiveFilters.hidden = labels.length === 0;
}

function renderControls() {
  els.searchInput.value = state.prefs.search;
  els.autoRefreshToggle.checked = state.prefs.autoRefresh;
  els.refreshInterval.value = String(state.prefs.refreshIntervalMs);
  if (document.activeElement !== els.categoryInput) els.categoryInput.value = state.prefs.lastCategory || "";
  if (document.activeElement !== els.tagsInput) els.tagsInput.value = state.prefs.lastTags || "";
  fillSelect(els.statusFilter, Object.entries(STATUS_GROUPS), state.prefs.status);
  fillSelect(els.categoryFilter, [["all", "Toutes"], ...uniqueValues((torrent) => [String(torrent.category || "").trim()]).map((value) => [value, value])], state.prefs.category);
  fillSelect(els.tagFilter, [["all", "Tous"], ...uniqueValues((torrent) => normalizeTags(torrent.tags)).map((value) => [value, value])], state.prefs.tag);
  els.sortSelect.replaceChildren(
    ...Object.entries(SORT_LABELS).map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }),
  );
  els.sortSelect.value = state.prefs.sort;
  renderQuickFilters();
  renderFilterNotice();
}

function filteredTorrents() {
  const search = state.prefs.search.trim().toLocaleLowerCase("fr");
  const filtered = state.torrents.filter((torrent) => {
    const meta = stateMeta(torrent);
    if (search && !String(torrent.name || "").toLocaleLowerCase("fr").includes(search)) return false;
    if (state.prefs.status === "complete" && !isComplete(torrent)) return false;
    if (state.prefs.status === "active" && !["downloading", "waiting", "checking", "sharing"].includes(meta.group)) return false;
    if (!["all", "complete", "active"].includes(state.prefs.status) && meta.group !== state.prefs.status) return false;
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
  return [...torrents].sort((leftTorrent, rightTorrent) => {
    if (key === "default") {
      const groupDiff = (GROUP_ORDER[stateMeta(leftTorrent).group] ?? 99) - (GROUP_ORDER[stateMeta(rightTorrent).group] ?? 99);
      if (groupDiff) return groupDiff;
      return (Number(rightTorrent.downloadSpeed) || 0) - (Number(leftTorrent.downloadSpeed) || 0);
    }
    const left = sortValue(leftTorrent, key);
    const right = sortValue(rightTorrent, key);
    if (typeof left === "string" || typeof right === "string") return String(left).localeCompare(String(right), "fr") * direction;
    return (left - right) * direction;
  });
}

function setStatusFromQuickFilter(filterKey) {
  state.prefs.status = filterKey;
  savePrefs();
  updateUrl();
  render();
}

function setSort(key) {
  if (state.prefs.sort === key) {
    state.prefs.direction = state.prefs.direction === "asc" ? "desc" : "asc";
  } else {
    state.prefs.sort = key;
    state.prefs.direction = key === "name" ? "asc" : "desc";
  }
  savePrefs();
  updateUrl();
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

function quickActionButton(item) {
  const action = button(item.label, "secondary", async (event) => {
    const trigger = event.currentTarget;
    trigger.disabled = true;
    const previous = trigger.textContent;
    trigger.textContent = "Lancement…";
    try {
      const payload = await api(`api/media-actions/${item.actionId}`, { method: "POST" });
      showToast(payload.message || "Action lancée.");
      await loadTorrents({ silent: true, force: true });
    } catch (error) {
      showError(error);
    } finally {
      trigger.disabled = false;
      trigger.textContent = previous;
    }
  });
  action.className = "quick-action quick-action-button";
  const title = document.createElement("strong");
  title.textContent = item.label;
  const subtitle = document.createElement("span");
  subtitle.textContent = item.description || "Déclenche une action backend.";
  action.replaceChildren(title, subtitle);
  return action;
}

function actionLink(action) {
  const link = document.createElement("a");
  link.className = "button secondary";
  link.href = action?.url || "/torrent-panel/?view=home";
  link.textContent = action?.label || "Afficher";
  return link;
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
  const isForcedShare = torrent.state === "forcedUP";
  const canForceShare = isComplete(torrent) || isForcedShare;
  const pauseOrResume = button(isPaused ? "Reprendre" : "Pause", isPaused ? "primary" : "secondary", () => runTorrentAction([hash], isPaused ? "resume" : "pause"));
  const detail = button("Détails", "secondary", (event) => openDetails(hash, event.currentTarget));
  const forceShare = canForceShare
    ? button(isForcedShare ? "Partage normal" : "Partage forcé", isForcedShare ? "primary" : "secondary", () => runTorrentAction([hash], "force-start", { enabled: !isForcedShare }))
    : null;
  const remove = button("Supprimer", "danger", (event) => openDeleteDialog([torrent], event.currentTarget));
  for (const control of [pauseOrResume, detail, forceShare, remove].filter(Boolean)) control.disabled = Boolean(busyText);
  actions.append(...[pauseOrResume, detail, forceShare, remove].filter(Boolean));
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

function renderQuickActions() {
  els.quickActions.replaceChildren(
    ...(state.dashboard.quickActions || []).map((item) => {
      if (item.kind === "api" && item.actionId) return quickActionButton(item);
      const link = document.createElement("a");
      link.className = "quick-action";
      link.href = item.url || "/torrent-panel/?view=home";
      const title = document.createElement("strong");
      title.textContent = item.label;
      const subtitle = document.createElement("span");
      subtitle.textContent = item.description || (item.id === "refresh-all" ? "Relance la vérification de tous les services." : "Ouvre directement la bonne vue.");
      link.append(title, subtitle);
      return link;
    }),
  );
}

function renderAlerts() {
  const alerts = Array.isArray(state.dashboard.alerts) ? state.dashboard.alerts : [];
  const critical = alerts.filter((item) => item.severity === "critical");
  els.alertsSummary.textContent = `${alerts.length} alerte(s), dont ${critical.length} critique(s).`;
  els.navAlertCount.hidden = critical.length === 0;
  els.navAlertCount.textContent = String(critical.length);
  els.criticalAlerts.hidden = critical.length === 0;
  els.criticalAlerts.replaceChildren(
    ...critical.map((item) => {
      const article = document.createElement("article");
      article.className = "alert-item critical";
      const head = document.createElement("div");
      head.className = "alert-head";
      const title = document.createElement("strong");
      title.textContent = item.service;
      const severity = document.createElement("span");
      severity.className = "severity-pill critical";
      severity.textContent = "Critique";
      head.append(title, severity);
      const text = document.createElement("p");
      text.textContent = item.message;
      article.append(head, text, actionLink(item.action));
      return article;
    }),
  );
  els.alertsList.replaceChildren(
    ...alerts.map((item) => {
      const article = document.createElement("article");
      article.className = `alert-item ${item.severity || "warning"}`;
      const head = document.createElement("div");
      head.className = "alert-head";
      const title = document.createElement("strong");
      title.textContent = item.service;
      const severity = document.createElement("span");
      severity.className = `severity-pill ${item.severity || "warning"}`;
      severity.textContent = item.severity === "critical" ? "Critique" : "Alerte";
      head.append(title, severity);
      const text = document.createElement("p");
      text.textContent = item.message;
      const meta = document.createElement("div");
      meta.className = "service-meta";
      meta.textContent = formatIsoDate(item.date);
      article.append(head, text, meta, actionLink(item.action));
      return article;
    }),
  );
}

function renderServices() {
  const services = Array.isArray(state.dashboard.services) ? state.dashboard.services : [];
  const operational = services.filter((item) => item.status === "operational").length;
  els.servicesSummary.textContent = `${operational}/${services.length} service(s) opérationnel(s).`;
  els.servicesGrid.replaceChildren(
    ...services.map((item) => {
      const article = document.createElement("article");
      article.className = `service-item ${item.status || "checking"}`;
      const head = document.createElement("div");
      head.className = "service-head";
      const title = document.createElement("strong");
      title.textContent = item.name;
      head.append(title, renderBadge({
        group: item.status === "operational" ? "complete" : item.status === "degraded" ? "checking" : item.status === "checking" ? "waiting" : "error",
        text: item.status === "operational" ? "Opérationnel" : item.status === "degraded" ? "Dégradé" : item.status === "checking" ? "Vérification en cours" : "Indisponible",
        icon: item.status === "operational" ? "✓" : item.status === "degraded" ? "…" : item.status === "checking" ? "…" : "!",
      }));
      const text = document.createElement("p");
      text.textContent = item.message;
      const meta = document.createElement("div");
      meta.className = "service-meta";
      meta.textContent = `Dernière vérification: ${formatIsoDate(item.checkedAt)} · Dernier succès: ${formatIsoDate(item.lastSuccessfulCheckAt)}`;
      article.append(head, text, meta, actionLink(item.action));
      return article;
    }),
  );
}

function workflowBadge(status) {
  const mapping = {
    pending: { group: "waiting", text: "En attente", icon: "…" },
    rclone_refresh: { group: "checking", text: "Actualisation rclone", icon: "…" },
    mount_wait: { group: "checking", text: "Attente du montage", icon: "…" },
    jellyfin_requested: { group: "checking", text: "Scan Jellyfin demandé", icon: "…" },
    completed: { group: "complete", text: "Terminé", icon: "✓" },
    partial_failure: { group: "error", text: "Échec partiel", icon: "!" },
    failed: { group: "error", text: "Échec définitif", icon: "!" },
  };
  return renderBadge(mapping[status] || mapping.pending);
}

function retryWorkflow(entryId, scope, trigger) {
  if (trigger) {
    trigger.disabled = true;
    trigger.textContent = "Relance…";
  }
  return api(`api/media-workflows/${entryId}/retry`, {
    method: "POST",
    body: JSON.stringify({ scope }),
  }).then(async () => {
    showToast(scope === "jellyfin" ? "Scan Jellyfin relancé." : "Workflow relancé.");
    await loadTorrents({ silent: true, force: true });
  }).catch((error) => {
    showError(error);
  }).finally(() => {
    if (trigger) {
      trigger.disabled = false;
      trigger.textContent = scope === "jellyfin" ? "Relancer Jellyfin" : "Réessayer";
    }
  });
}

function renderMediaAutomation() {
  const payload = state.dashboard.mediaAutomation || { enabled: false, entries: [], notification: null };
  const entries = Array.isArray(payload.entries) ? payload.entries : [];
  if (!payload.enabled) {
    els.mediaAutomationSummary.textContent = "Workflow désactivé côté backend.";
    els.mediaAutomationNotice.hidden = true;
    els.mediaAutomationList.replaceChildren();
    return;
  }
  els.mediaAutomationSummary.textContent = entries.length
    ? `${entries.length} événement(s) conservé(s) dans l'historique récent.`
    : "Aucun téléchargement terminé détecté récemment.";
  const notice = payload.notification;
  if (notice?.message) {
    els.mediaAutomationNotice.hidden = false;
    els.mediaAutomationNotice.className = `media-notice ${notice.severity || "info"}`;
    els.mediaAutomationNotice.textContent = `${notice.message} · ${formatIsoDate(notice.date)}`;
  } else {
    els.mediaAutomationNotice.hidden = true;
  }
  els.mediaAutomationList.replaceChildren(
    ...entries.map((entry) => {
      const article = document.createElement("article");
      article.className = "media-item";
      const head = document.createElement("div");
      head.className = "service-head";
      const title = document.createElement("strong");
      title.textContent = entry.torrentName || "Torrent";
      head.append(title, workflowBadge(entry.state));
      const meta = document.createElement("div");
      meta.className = "service-meta";
      meta.textContent = `Fin: ${formatIsoDate(entry.completedAt)} · Catégorie: ${entry.category || "—"}`;
      const details = document.createElement("div");
      details.className = "media-steps";
      const lines = [
        `rclone: ${entry.rclone?.result || "—"}`,
        `montage: ${entry.mount?.result || "—"}`,
        `Jellyfin: ${entry.jellyfin?.result || "—"}`,
        `bibliothèque: ${entry.jellyfin?.library || (entry.jellyfin?.scope === "global" ? "globale" : "—")}`,
      ];
      if (entry.errorMessage) lines.push(`erreur: ${entry.errorMessage}`);
      details.textContent = lines.join(" · ");
      const actions = document.createElement("div");
      actions.className = "action-row";
      if (entry.retry?.full) {
        actions.append(button("Réessayer", "secondary", (event) => retryWorkflow(entry.id, "full", event.currentTarget)));
      }
      if (entry.retry?.jellyfin) {
        actions.append(button("Relancer Jellyfin", "secondary", (event) => retryWorkflow(entry.id, "jellyfin", event.currentTarget)));
      }
      article.append(head, meta, details);
      if (actions.childNodes.length) article.append(actions);
      return article;
    }),
  );
}

function renderHome() {
  const critical = Number(state.dashboard.criticalCount || 0);
  els.homeSummary.textContent = critical
    ? `${critical} alerte(s) critique(s) demandent une attention immédiate.`
    : "Aucune alerte critique. Les services restent surveillés en direct.";
  renderQuickActions();
  renderAlerts();
  renderServices();
  renderMediaAutomation();
}

function render() {
  renderSummary();
  renderControls();
  renderHome();
  setView(state.activeView, { push: false });
  const visible = filteredTorrents();
  els.rows.replaceChildren(...visible.map(renderRow));
  els.empty.textContent = state.torrents.length === 0 ? "Aucun torrent pour le moment." : "Aucun torrent ne correspond aux filtres.";
  els.empty.hidden = visible.length !== 0;
  els.summary.textContent = `${visible.length} affiché${visible.length > 1 ? "s" : ""} sur ${state.torrents.length}`;
  renderSelection(visible);
  renderSortHeaders();
  updateDetails();
}

async function loadDashboard() {
  const payload = await api("api/dashboard", { cache: "no-store" });
  state.dashboard = {
    alerts: Array.isArray(payload.alerts) ? payload.alerts : [],
    criticalCount: Number(payload.criticalCount) || 0,
    quickActions: Array.isArray(payload.quickActions) ? payload.quickActions : [],
    services: Array.isArray(payload.services) ? payload.services : [],
    mediaAutomation: payload.mediaAutomation && typeof payload.mediaAutomation === "object"
      ? payload.mediaAutomation
      : { enabled: false, entries: [], notification: null },
  };
}

async function loadTorrents({ silent = false, force = false } = {}) {
  if (state.refreshPromise) return state.refreshPromise;
  if (!force && (state.globalActionCount > 0 || document.hidden)) return null;
  if (!silent) els.refreshStatus.textContent = "Actualisation...";

  state.refreshPromise = (async () => {
    try {
      const [dashboardPayload, torrentPayload] = await Promise.all([
        api("api/dashboard", { cache: "no-store" }),
        api("api/torrents", { cache: "no-store" }),
      ]);
      state.dashboard = {
        alerts: Array.isArray(dashboardPayload.alerts) ? dashboardPayload.alerts : [],
        criticalCount: Number(dashboardPayload.criticalCount) || 0,
        quickActions: Array.isArray(dashboardPayload.quickActions) ? dashboardPayload.quickActions : [],
        services: Array.isArray(dashboardPayload.services) ? dashboardPayload.services : [],
        mediaAutomation: dashboardPayload.mediaAutomation && typeof dashboardPayload.mediaAutomation === "object"
          ? dashboardPayload.mediaAutomation
          : { enabled: false, entries: [], notification: null },
      };
      const torrents = Array.isArray(torrentPayload.torrents) ? torrentPayload.torrents : [];
      const signature = JSON.stringify(torrents);
      state.lastUpdatedAt = new Date();
      if (signature !== state.lastSignature) {
        state.torrents = torrents;
        state.lastSignature = signature;
      }
      clearError();
      render();
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
    "force-start": "Mise à jour du partage forcé…",
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
    showToast(
      action === "pause"
        ? "Torrent mis en pause."
        : action === "resume"
          ? "Torrent repris."
          : action === "force-start"
            ? (options.enabled ? "Partage forcé activé." : "Partage forcé désactivé.")
            : "Torrent supprimé.",
    );
    hashes.forEach((hash) => state.selected.delete(hash));
    await loadTorrents({ silent: true, force: true });
  } catch (error) {
    hashes.forEach((hash) => state.rowErrors.set(hash, describeError(error)));
    render();
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
  els.deleteTorrentName.textContent = torrents.length > 1 ? `${torrents.length} torrents sélectionnés` : torrents[0]?.name || "";
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
    state.prefs.lastCategory = els.categoryInput.value.trim();
    state.prefs.lastTags = els.tagsInput.value.trim();
    savePrefs();
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
  updateUrl();
  render();
}

function resetFilters() {
  state.prefs = { ...state.prefs, search: "", status: "all", category: "all", tag: "all", sort: "default", direction: "asc" };
  savePrefs();
  updateUrl();
  render();
}

function openAddPanel() {
  const addPanel = document.querySelector("#addPanel");
  if (addPanel) addPanel.open = true;
  state.activeView = "torrents";
  setView("torrents");
  els.magnetInput.focus();
}

function bindEvents() {
  els.refreshButton.addEventListener("click", () => loadTorrents({ force: true }));
  els.openAddPanelButton.addEventListener("click", openAddPanel);
  els.retryButton.addEventListener("click", () => loadTorrents({ force: true }));
  els.searchInput.addEventListener("input", () => updatePreference("search", els.searchInput.value));
  els.statusFilter.addEventListener("change", () => updatePreference("status", els.statusFilter.value));
  els.categoryFilter.addEventListener("change", () => updatePreference("category", els.categoryFilter.value));
  els.tagFilter.addEventListener("change", () => updatePreference("tag", els.tagFilter.value));
  els.sortSelect.addEventListener("change", () => updatePreference("sort", els.sortSelect.value));
  els.resetFilters.addEventListener("click", resetFilters);
  els.clearActiveFilters.addEventListener("click", resetFilters);
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
  document.querySelectorAll(".sort-head").forEach((head) => head.addEventListener("click", () => setSort(head.dataset.sort)));
  els.quickFilters.addEventListener("click", (event) => {
    const clicked = event.target.closest("[data-quick-filter]");
    if (!clicked) return;
    setStatusFromQuickFilter(clicked.dataset.quickFilter);
  });
  els.summaryGrid.addEventListener("click", (event) => {
    const clicked = event.target.closest("[data-filter]");
    if (!clicked) return;
    state.activeView = "torrents";
    setView("torrents");
    setStatusFromQuickFilter(clicked.dataset.filter);
  });
  els.selectVisible.addEventListener("change", () => {
    const visible = filteredTorrents();
    if (els.selectVisible.checked) visible.forEach((torrent) => state.selected.add(torrent.hash));
    else visible.forEach((torrent) => state.selected.delete(torrent.hash));
    render();
  });
  els.bulkPause.addEventListener("click", () => runTorrentAction([...state.selected], "pause"));
  els.bulkResume.addEventListener("click", () => runTorrentAction([...state.selected], "resume"));
  els.bulkForceShare.addEventListener("click", () => {
    const selectedTorrents = state.torrents.filter((torrent) => state.selected.has(torrent.hash));
    const eligible = selectedTorrents.filter((torrent) => isComplete(torrent) || torrent.state === "forcedUP");
    if (!eligible.length) {
      showToast("Sélectionnez au moins un torrent terminé pour utiliser le partage forcé.");
      return;
    }
    const disableForcedShare = eligible.every((torrent) => torrent.state === "forcedUP");
    runTorrentAction(eligible.map((torrent) => torrent.hash), "force-start", { enabled: !disableForcedShare });
  });
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
  els.confirmDeleteButton.addEventListener("click", confirmDelete);
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
  window.addEventListener?.("popstate", () => {
    applyUrlState();
    render();
  });
}

async function init() {
  bindEvents();
  applyUrlState();
  renderControls();
  restartRefreshTimer();
  try {
    await refreshSession();
    await loadTorrents({ force: true });
    const url = currentUrl();
    if (url.searchParams.get("add") === "1") openAddPanel();
    if (url.searchParams.get("refresh") === "1") await loadTorrents({ force: true });
  } catch (error) {
    try {
      await loadDashboard();
      render();
    } catch {}
    showError(error);
    els.refreshStatus.textContent = "Erreur";
  }
}

init();

globalThis.__testApi = { formatBytes, formatSpeed, formatRatio, formatEta, stateMeta, filteredTorrents, state };
