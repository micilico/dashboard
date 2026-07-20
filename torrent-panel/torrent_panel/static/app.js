const panelConfig = window.__TORRENT_PANEL_CONFIG__ || {};
const STORAGE_KEY = "torrent-panel-preferences";
const DEFAULT_PREFS = {
  search: "",
  status: "all",
  category: "all",
  tag: "all",
  tracker: "all",
  sort: "default",
  direction: "asc",
  autoRefresh: true,
  refreshIntervalMs: 6000,
  lastCategory: "",
  lastTags: "",
};

const state = {
  publicPrefix: String(panelConfig.publicPrefix || "/torrent-panel").replace(/\/$/, ""),
  prowlarrPanelPrefix: String(panelConfig.prowlarrPanelPrefix || "/prowlarr-panel").replace(/\/$/, ""),
  csrfToken: "",
  activeView: "home",
  torrents: [],
  dashboard: {
    alerts: [],
    criticalCount: 0,
    overview: {},
    recentActivity: [],
    services: [],
    storage: {},
    quickActions: [],
    mediaAutomation: { enabled: false, entries: [], notification: null },
  },
  storage: { disk: {}, rclone: {} },
  activity: { summary: {}, timeline: [] },
  metricHistory: {
    downloadSpeedBytes: [],
    uploadSpeedBytes: [],
    activeTorrents: [],
  },
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
  pendingReleaseTitle: "",
  sourceHint: "",
  trackerIndex: { index: {}, domains: {} },
  prefs: loadPrefs(),
};

const els = {
  homeView: document.querySelector("#homeView"),
  torrentsView: document.querySelector("#torrentsView"),
  homeTitle: document.querySelector("#homeTitle"),
  homeSummary: document.querySelector("#homeSummary"),
  homeStatusBadge: document.querySelector("#homeStatusBadge"),
  homeStatusText: document.querySelector("#homeStatusText"),
  criticalAlerts: document.querySelector("#criticalAlerts"),
  servicesGrid: document.querySelector("#servicesGrid"),
  servicesSummary: document.querySelector("#servicesSummary"),
  servicesLink: document.querySelector("#servicesLink"),
  activitySummary: document.querySelector("#activitySummary"),
  activityList: document.querySelector("#activityList"),
  overviewMetrics: document.querySelector("#overviewMetrics"),
  homeNavLink: document.querySelector("#homeNavLink"),
  activityNavLink: document.querySelector("#activityNavLink"),
  torrentsNavLink: document.querySelector("#torrentsNavLink"),
  prowlarrNavLink: document.querySelector("#prowlarrNavLink"),
  storageNavLink: document.querySelector("#storageNavLink"),
  mediaNavLink: document.querySelector("#mediaNavLink"),
  healthNavLink: document.querySelector("#healthNavLink"),
  navAlertCount: document.querySelector("#navAlertCount"),
  statusText: document.querySelector("#statusText"),
  lastCheck: document.querySelector("#lastCheck"),
  rows: document.querySelector("#torrentRows"),
  summary: document.querySelector("#summary"),
  summaryGrid: document.querySelector("#summaryGrid"),
  alert: document.querySelector("#alert"),
  alertText: document.querySelector("#alertText"),
  retryButton: document.querySelector("#retryButton"),
  empty: document.querySelector("#emptyState"),
  refreshStatus: document.querySelector("#refreshStatus"),
  sidebarStatusDetail: document.querySelector("#sidebarStatusDetail"),
  refreshButton: document.querySelector("#refreshButton"),
  openAddPanelButton: document.querySelector("#openAddPanelButton"),
  addTorrentButton: document.querySelector("#addTorrentButton"),
  autoRefreshToggle: document.querySelector("#autoRefreshToggle"),
  refreshInterval: document.querySelector("#refreshInterval"),
  searchInput: document.querySelector("#searchInput"),
  statusFilter: document.querySelector("#statusFilter"),
  categoryFilter: document.querySelector("#categoryFilter"),
  tagFilter: document.querySelector("#tagFilter"),
  trackerFilter: document.querySelector("#trackerFilter"),
  sortSelect: document.querySelector("#sortSelect"),
  resetFilters: document.querySelector("#resetFilters"),
  clearActiveFilters: document.querySelector("#clearActiveFilters"),
  quickFilters: document.querySelector("#quickFilters"),
  filterNotice: document.querySelector("#filterNotice"),
  followNotice: document.querySelector("#followNotice"),
  selectVisible: document.querySelector("#selectVisible"),
  selectVisibleLabel: document.querySelector("#selectVisibleLabel"),
  visibleSelectionSummary: document.querySelector("#visibleSelectionSummary"),
  bulkBar: document.querySelector("#bulkBar"),
  bulkCount: document.querySelector("#bulkCount"),
  bulkPause: document.querySelector("#bulkPause"),
  bulkResume: document.querySelector("#bulkResume"),
  bulkForceShare: document.querySelector("#bulkForceShare"),
  bulkAddTracker: document.querySelector("#bulkAddTracker"),
  bulkDelete: document.querySelector("#bulkDelete"),
  addTrackerDialog: document.querySelector("#addTrackerDialog"),
  addTrackerTitle: document.querySelector("#addTrackerTitle"),
  addTrackerCount: document.querySelector("#addTrackerCount"),
  addTrackerUrlInput: document.querySelector("#addTrackerUrlInput"),
  addTrackerMessage: document.querySelector("#addTrackerMessage"),
  cancelAddTrackerButton: document.querySelector("#cancelAddTrackerButton"),
  confirmAddTrackerButton: document.querySelector("#confirmAddTrackerButton"),
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

function formatRelativeTime(value) {
  if (!value) return "A l'instant";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Date inconnue";
  const diffSeconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
  if (diffSeconds < 45) return "A l'instant";
  if (diffSeconds < 3600) return `Il y a ${Math.max(1, Math.round(diffSeconds / 60))} min`;
  if (diffSeconds < 86400) {
    const hours = Math.round(diffSeconds / 3600);
    return `Il y a ${hours} h`;
  }
  return `Il y a ${Math.round(diffSeconds / 86400)} j`;
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

function route(path = "/") {
  if (path === "/") return `${state.publicPrefix || ""}/`;
  return `${state.publicPrefix}${path.startsWith("/") ? path : `/${path}`}`;
}

async function api(path, options = {}, retryCsrf = true) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if ((options.method || "GET").toUpperCase() !== "GET") headers.set("X-Torrent-Panel-CSRF", state.csrfToken);

  const response = await fetchWithRetry(path, { ...options, headers, credentials: "same-origin" });
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
  const session = await api(route("/api/session"), { cache: "no-store" }, false);
  state.csrfToken = session.csrfToken;
}

function currentUrl() {
  const href = window.location?.href || `http://localhost${route("/")}`;
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
      tracker: state.prefs.tracker,
      sort: state.prefs.sort,
      direction: state.prefs.direction,
    })) {
      if (value && value !== DEFAULT_PREFS[key]) url.searchParams.set(key, value);
      else url.searchParams.delete(key);
    }
  } else {
    ["search", "status", "category", "tag", "tracker", "sort", "direction"].forEach((key) => url.searchParams.delete(key));
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
  const tracker = url.searchParams.get("tracker");
  const sort = url.searchParams.get("sort");
  const direction = url.searchParams.get("direction");
  state.pendingReleaseTitle = url.searchParams.get("pendingRelease") || "";
  state.sourceHint = url.searchParams.get("source") || "";
  if (search !== null) state.prefs.search = search;
  if (status !== null) state.prefs.status = status;
  if (category !== null) state.prefs.category = category;
  if (tag !== null) state.prefs.tag = tag;
  if (tracker !== null) state.prefs.tracker = tracker;
  if (sort !== null) state.prefs.sort = sort;
  if (direction !== null) state.prefs.direction = direction;
}

function setView(view, { push = true } = {}) {
  state.activeView = view === "torrents" ? "torrents" : "home";
  els.homeView.hidden = state.activeView !== "home";
  els.torrentsView.hidden = state.activeView !== "torrents";
  els.summaryGrid.hidden = true;
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
  if (state.prefs.tracker !== "all") {
    const domainCount = state.trackerIndex.domains[state.prefs.tracker];
    labels.push(`Tracker : ${state.prefs.tracker}${domainCount ? ` (${domainCount})` : ""}`);
  }
  return labels;
}

function renderFilterChips() {
  const container = document.querySelector("#activeFilters");
  if (!container) return;
  const labels = activeFilterLabels();
  container.replaceChildren(
    ...labels.map((label) => {
      const chip = document.createElement("span");
      chip.className = "filter-chip";
      const text = document.createElement("span");
      text.textContent = label;
      const close = document.createElement("button");
      close.type = "button";
      close.textContent = "×";
      close.setAttribute("aria-label", `Retirer le filtre : ${label}`);
      close.addEventListener("click", resetFilters);
      chip.append(text, close);
      return chip;
    }),
  );
}

function renderFilterNotice() {
  const labels = activeFilterLabels();
  els.filterNotice.textContent = labels.length ? `Filtres actifs : ${labels.join(" · ")}` : "Aucun filtre actif.";
  els.clearActiveFilters.hidden = labels.length === 0;
  renderFilterChips();
}

function renderFollowNotice() {
  if (!els.followNotice) return;
  if (state.sourceHint !== "prowlarr" || !state.pendingReleaseTitle) {
    els.followNotice.hidden = true;
    els.followNotice.replaceChildren();
    return;
  }
  const backLink = document.createElement("a");
  backLink.className = "button secondary";
  backLink.href = `${state.prowlarrPanelPrefix || "/prowlarr-panel"}/?view=search&query=${encodeURIComponent(state.pendingReleaseTitle)}`;
  backLink.textContent = "Retourner a Prowlarr";
  els.followNotice.hidden = false;
  els.followNotice.replaceChildren(
    Object.assign(document.createElement("span"), {
      textContent: `Suivi cible pour "${state.pendingReleaseTitle}". L'apparition dans qBittorrent peut prendre quelques secondes.`,
    }),
    backLink,
  );
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
  const domainEntries = Object.entries(state.trackerIndex.domains).sort(([a], [b]) => a.localeCompare(b, "fr"));
  fillSelect(els.trackerFilter, [["all", "Tous les trackers"], ["__none__", "Sans tracker"], ...domainEntries.map(([domain, count]) => [domain, `${domain} — ${count}`])], state.prefs.tracker);
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
  renderFollowNotice();
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
    if (state.prefs.tracker === "__none__") {
      const trackerDomains = state.trackerIndex.index[torrent.hash];
      if (trackerDomains && trackerDomains.length > 0) return false;
    } else if (state.prefs.tracker !== "all") {
      const trackerDomains = state.trackerIndex.index[torrent.hash];
      if (!trackerDomains || !trackerDomains.includes(state.prefs.tracker)) return false;
    }
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
  link.href = action?.url || `${route("/")}?view=home`;
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
  const allVisibleSelected = visibleHashes.length > 0 && selectedVisible.length === visibleHashes.length;
  els.selectVisible.checked = allVisibleSelected;
  els.selectVisible.indeterminate = selectedVisible.length > 0 && selectedVisible.length < visibleHashes.length;
  els.selectVisible.disabled = visibleHashes.length === 0;
  els.selectVisibleLabel.textContent = allVisibleSelected ? "Tout désélectionner" : "Tout sélectionner";
  els.visibleSelectionSummary.textContent = visibleHashes.length
    ? `${selectedVisible.length} sur ${visibleHashes.length} sélectionné${selectedVisible.length > 1 ? "s" : ""}`
    : "Aucun fichier affiché";
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

function createSvgIcon(name) {
  const span = document.createElement("span");
  span.className = `ui-icon ui-icon-${name}`;
  span.setAttribute("aria-hidden", "true");
  const icons = {
    speed: `<svg viewBox="0 0 24 24"><path d="M4 15.5 9 10.5l3.5 3.5L20 6.5"></path><path d="M14 6.5h6v6"></path></svg>`,
    torrents: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.25"></circle><path d="M12 7v8m0 0 3-3m-3 3-3-3"></path></svg>`,
    indexers: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.25"></circle><path d="M3.75 12h16.5M12 3.75c2.5 2.6 3.75 5.35 3.75 8.25S14.5 17.65 12 20.25M12 3.75c-2.5 2.6-3.75 5.35-3.75 8.25S9.5 17.65 12 20.25"></path></svg>`,
    jellyfin: `<svg viewBox="0 0 24 24"><path d="m12 4 7 14H5z"></path><path d="m12 9 2.75 5.5h-5.5z"></path></svg>`,
    qBittorrent: `<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8.25"></circle><path d="M8.5 8.5v7M15.5 8.5v7M8.5 12c0-1.93 1.57-3.5 3.5-3.5 1.8 0 3.28 1.36 3.47 3.11"></path></svg>`,
    prowlarr: `<svg viewBox="0 0 24 24"><path d="m12 4 6.75 3.9v8.2L12 20l-6.75-3.9V7.9z"></path><path d="m12 4 6.75 12.1M12 4 5.25 16.1M5.25 7.9 12 12l6.75-4.1M12 12v8"></path></svg>`,
    rclone: `<svg viewBox="0 0 24 24"><path d="M8.5 18.25h8.25a3.25 3.25 0 0 0 .6-6.44A4.75 4.75 0 0 0 8 10.5a3.5 3.5 0 0 0 .5 6.94Z"></path></svg>`,
    download: `<svg viewBox="0 0 24 24"><path d="M12 4v10m0 0 3.5-3.5M12 14l-3.5-3.5M5 18.25h14"></path></svg>`,
    upload: `<svg viewBox="0 0 24 24"><path d="M12 20V10m0 0 3.5 3.5M12 10l-3.5 3.5M5 5.75h14"></path></svg>`,
    alert: `<svg viewBox="0 0 24 24"><path d="m12 4 8 14H4z"></path><path d="M12 9v4.5M12 17h.01"></path></svg>`,
    scan: `<svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4.25 4.25"></path></svg>`,
    play: `<svg viewBox="0 0 24 24"><path d="m9 7 8 5-8 5z"></path></svg>`,
  };
  span.innerHTML = icons[name] || icons.scan;
  return span;
}

function renderAlerts() {
  const alerts = Array.isArray(state.dashboard.alerts) ? state.dashboard.alerts : [];
  const critical = alerts.filter((item) => item.severity === "critical");
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
}

function renderServices() {
  const services = Array.isArray(state.dashboard.services) ? state.dashboard.services : [];
  const orderedNames = ["qBittorrent", "Prowlarr", "rclone", "Jellyfin"];
  const visibleServices = orderedNames
    .map((name) => services.find((item) => String(item.name || "").toLowerCase() === name.toLowerCase()))
    .filter(Boolean);
  const operational = services.filter((item) => item.status === "operational").length;
  els.servicesSummary.textContent = `${operational}/${services.length} service(s) opérationnel(s).`;
  els.servicesGrid.replaceChildren(
    ...visibleServices.map((item) => {
      const link = document.createElement(item.action?.url ? "a" : "div");
      link.className = `service-row ${item.status || "checking"}${item.action?.url ? " card-interactive" : ""}`;
      if (item.action?.url) link.href = item.action.url;
      const left = document.createElement("div");
      left.className = "service-row-main";
      left.append(
        createSvgIcon(item.name === "qBittorrent" ? "qBittorrent" : String(item.name || "").toLowerCase()),
      );
      const copy = document.createElement("div");
      copy.className = "service-row-copy";
      const title = document.createElement("strong");
      title.textContent = item.name;
      const meta = document.createElement("span");
      meta.textContent = formatRelativeTime(item.checkedAt);
      copy.append(title, meta);
      left.append(copy);
      const status = document.createElement("div");
      status.className = "service-row-status";
      const dot = document.createElement("span");
      dot.className = `status-dot ${item.status || "checking"}`;
      const text = document.createElement("span");
      text.textContent = item.status === "operational"
        ? "Opérationnel"
        : item.status === "degraded"
          ? "Dégradé"
          : item.status === "checking"
            ? "En attente"
            : "Indisponible";
      status.append(dot, text);
      link.append(left, status);
      return link;
    }).slice(0, 4),
  );
}

function pushMetricHistory(key, value) {
  const series = state.metricHistory[key] || [];
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return;
  series.push(numeric);
  if (series.length > 16) series.shift();
  state.metricHistory[key] = series;
}

function sparklineSvg(points) {
  const values = points.length >= 2 ? points : [0, 0];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const path = values.map((value, index) => {
    const x = (index / Math.max(values.length - 1, 1)) * 100;
    const y = 100 - ((value - min) / range) * 72 - 14;
    return `${index === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(" ");
  return `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><path d="${path}" pathLength="100"></path></svg>`;
}

function trendText(points) {
  if (points.length < 2) return "Historique en cours";
  const diff = points.at(-1) - points.at(0);
  if (Math.abs(diff) < 0.001) return "Tendance stable";
  return diff > 0 ? "Tendance en hausse" : "Tendance en baisse";
}

function renderOverviewMetrics() {
  const overview = state.dashboard.overview || {};
  pushMetricHistory("downloadSpeedBytes", Number(overview.downloadSpeedBytes || 0));
  pushMetricHistory("uploadSpeedBytes", Number(overview.uploadSpeedBytes || 0));
  pushMetricHistory("activeTorrents", Number(overview.activeTorrents || 0));
  const cards = [
    {
      label: "Débit descendant",
      value: formatSpeed(overview.downloadSpeedBytes || 0),
      meta: "Téléchargement actuel",
      icon: "download",
      historyKey: "downloadSpeedBytes",
    },
    {
      label: "Débit montant",
      value: formatSpeed(overview.uploadSpeedBytes || 0),
      meta: "Envoi actuel",
      icon: "upload",
      historyKey: "uploadSpeedBytes",
    },
    {
      label: "Torrents actifs",
      value: String(overview.activeTorrents || 0),
      meta: "Téléchargements et partages",
      icon: "speed",
      historyKey: "activeTorrents",
    },
  ];
  els.overviewMetrics.replaceChildren(
    ...cards.map((card) => {
      const article = document.createElement("article");
      article.className = "overview-card metric-card";
      const top = document.createElement("div");
      top.className = "metric-top";
      const iconWrap = document.createElement("div");
      iconWrap.className = "metric-icon";
      iconWrap.append(createSvgIcon(card.icon));
      const copy = document.createElement("div");
      copy.className = "metric-copy";
      copy.append(
        Object.assign(document.createElement("span"), { className: "metric-label", textContent: card.label }),
        Object.assign(document.createElement("strong"), { className: "metric-value", textContent: card.value }),
      );
      top.append(iconWrap, copy);
      const sparkline = document.createElement("div");
      sparkline.className = "sparkline";
      const history = state.metricHistory[card.historyKey] || [];
      sparkline.innerHTML = sparklineSvg(history);
      sparkline.setAttribute("aria-label", trendText(history));
      article.append(top, sparkline, Object.assign(document.createElement("p"), { className: "metric-meta", textContent: card.meta }));
      return article;
    }),
  );
}

function renderRecentActivity() {
  const items = Array.isArray(state.dashboard.recentActivity) && state.dashboard.recentActivity.length
    ? state.dashboard.recentActivity
    : (state.activity.timeline || []).slice(0, 3).map((item) => ({
        date: item.date,
        service: item.service,
        title: item.message,
        detail: item.type,
        kind: item.type === "download_completed" ? "download" : "scan",
        status: item.result === "critical" ? "danger" : "success",
      }));
  els.activitySummary.textContent = items.length
    ? `${items.length} événement(s) récents affichés.`
    : "Aucun événement récent disponible.";
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "activity-empty";
    empty.textContent = "Activité indisponible pour le moment.";
    els.activityList.replaceChildren(empty);
    return;
  }
  els.activityList.replaceChildren(
    ...items.slice(0, 3).map((item) => {
      const article = document.createElement("article");
      article.className = "activity-item";
      const icon = document.createElement("div");
      icon.className = `activity-icon ${item.status || "success"}`;
      icon.append(createSvgIcon(item.kind === "download" ? "download" : item.kind === "alert" ? "alert" : item.kind === "play" ? "play" : "scan"));
      const copy = document.createElement("div");
      copy.className = "activity-copy";
      const title = document.createElement("strong");
      title.textContent = item.title || "Événement";
      const detail = document.createElement("p");
      detail.textContent = `${item.service || "Service"} · ${item.detail || "Mise à jour"}`;
      const meta = document.createElement("span");
      meta.textContent = formatRelativeTime(item.date);
      copy.append(title, detail, meta);
      article.append(icon, copy);
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

function renderHome() {
  const critical = Number(state.dashboard.criticalCount || 0);
  const operationalServices = (state.dashboard.services || []).filter((item) => item.status === "operational").length;
  const totalServices = (state.dashboard.services || []).length;
  els.homeTitle.innerHTML = critical ? "Une attention est requise." : "Tout fonctionne.<br>Parfaitement.";
  els.homeSummary.textContent = critical
    ? `${critical} alerte(s) critique(s) demandent une attention immédiate.`
    : "Aucune alerte critique. Les services restent surveillés en direct.";
  if (els.homeStatusBadge?.classList) {
    if (critical > 0) els.homeStatusBadge.classList.add("is-warning");
    else els.homeStatusBadge.classList.remove("is-warning");
  }
  els.homeStatusText.textContent = critical
    ? `${critical} incident(s) nécessitent une action`
    : "Tous les services sont opérationnels";
  const greeting = document.querySelector("#greeting");
  if (greeting) greeting.textContent = "Bonsoir";
  if (els.statusText) {
    els.statusText.textContent = critical
      ? `${critical} alerte(s) critique(s)`
      : "Tous les services sont opérationnels";
  }
  if (els.sidebarStatusDetail) {
    els.sidebarStatusDetail.textContent = totalServices
      ? `${operationalServices}/${totalServices} services surveillés.`
      : "Surveillance en direct.";
  }
  renderAlerts();
  renderOverviewMetrics();
  renderServices();
  renderRecentActivity();
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
  const payload = await api(route("/api/dashboard"), { cache: "no-store" });
  state.dashboard = {
    alerts: Array.isArray(payload.alerts) ? payload.alerts : [],
    criticalCount: Number(payload.criticalCount) || 0,
    overview: payload.overview && typeof payload.overview === "object" ? payload.overview : {},
    recentActivity: Array.isArray(payload.recentActivity) ? payload.recentActivity : [],
    storage: payload.storage && typeof payload.storage === "object" ? payload.storage : {},
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
      const [dashboardPayload, torrentPayload, storagePayload, activityPayload] = await Promise.all([
        api(route("/api/dashboard"), { cache: "no-store" }),
        api(route("/api/torrents"), { cache: "no-store" }),
        api(route("/api/storage"), { cache: "no-store" }).catch(() => ({ disk: state.storage.disk || {}, rclone: state.storage.rclone || {} })),
        api(route("/api/activity"), { cache: "no-store" }).catch(() => ({ summary: state.activity.summary || {}, timeline: state.activity.timeline || [] })),
      ]);
      state.dashboard = {
        alerts: Array.isArray(dashboardPayload.alerts) ? dashboardPayload.alerts : [],
        criticalCount: Number(dashboardPayload.criticalCount) || 0,
        overview: dashboardPayload.overview && typeof dashboardPayload.overview === "object" ? dashboardPayload.overview : {},
        recentActivity: Array.isArray(dashboardPayload.recentActivity) ? dashboardPayload.recentActivity : [],
        storage: dashboardPayload.storage && typeof dashboardPayload.storage === "object" ? dashboardPayload.storage : {},
        quickActions: Array.isArray(dashboardPayload.quickActions) ? dashboardPayload.quickActions : [],
        services: Array.isArray(dashboardPayload.services) ? dashboardPayload.services : [],
        mediaAutomation: dashboardPayload.mediaAutomation && typeof dashboardPayload.mediaAutomation === "object"
          ? dashboardPayload.mediaAutomation
          : { enabled: false, entries: [], notification: null },
      };
      state.storage = storagePayload && typeof storagePayload === "object" ? storagePayload : { disk: {}, rclone: {} };
      state.activity = activityPayload && typeof activityPayload === "object" ? activityPayload : { summary: {}, timeline: [] };
      const torrents = Array.isArray(torrentPayload.torrents) ? torrentPayload.torrents : [];
      const signature = JSON.stringify(torrents);
      state.lastUpdatedAt = new Date();
      if (signature !== state.lastSignature) {
        state.torrents = torrents;
        state.lastSignature = signature;
      }
      clearError();
      render();
      const lastCheck = els.lastCheck;
      if (lastCheck) {
        lastCheck.textContent = state.lastUpdatedAt.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" });
        lastCheck.setAttribute("datetime", state.lastUpdatedAt.toISOString());
      }
      if (!lastCheck) {
        els.refreshStatus.textContent = `À jour ${state.lastUpdatedAt.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "medium" })}`;
      }
    } catch (error) {
      showError(error);
      const lastCheck = els.lastCheck;
      if (lastCheck && state.lastUpdatedAt) {
        lastCheck.textContent = state.lastUpdatedAt.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
      } else if (!state.lastUpdatedAt) {
        els.refreshStatus.textContent = "Erreur";
      }
    } finally {
      state.refreshPromise = null;
    }
  })();
  return state.refreshPromise;
}

async function loadTrackerIndex() {
  try {
    const payload = await api(route("/api/trackers/index"), { cache: "no-store" });
    if (payload && typeof payload.index === "object" && typeof payload.domains === "object") {
      state.trackerIndex = { index: payload.index, domains: payload.domains };
      if (state.activeView === "torrents") render();
    }
  } catch {
    // Tracker index unavailable - filter will be empty
  }
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
    await api(route(`/api/torrents/${action}`), {
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
  if (typeof trapFocus === "function") trapFocus(els.deleteDialog);
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
    ["Limite DL", Number(torrent.downloadLimit) > 0 ? formatSpeed(torrent.downloadLimit) : "Illimitée"],
    ["Limite UL", Number(torrent.uploadLimit) > 0 ? formatSpeed(torrent.uploadLimit) : "Illimitée"],
    ["Séquentiel", torrent.sequentialDownload ? "Activé" : "Désactivé"],
    ["Chemin", torrent.savePath || "—"],
    ["Ajout", formatDate(torrent.addedOn)],
    ["Fin", formatDate(torrent.completionOn)],
  ];
}

async function runAdvancedAction(hash, action, payload = {}, successMessage = "Action appliquée.") {
  state.globalActionCount += 1;
  try {
    await api(route(`/api/torrents/${action}`), {
      method: "POST",
      body: JSON.stringify({ hashes: [hash], ...payload }),
    });
    showToast(successMessage);
    await loadTorrents({ silent: true, force: true });
  } catch (error) {
    showError(error);
  } finally {
    state.globalActionCount -= 1;
  }
}

function openDetails(hash, trigger) {
  state.detailHash = hash;
  state.lastFocus = trigger || document.activeElement;
  updateDetails();
  if (typeof trapFocus === "function") trapFocus(els.detailDialog);
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
  const advanced = document.createElement("section");
  advanced.className = "detail-advanced";
  const title = document.createElement("h3");
  title.textContent = "Plus";
  const actions = document.createElement("div");
  actions.className = "action-row";
  actions.append(
    button("Revérifier", "secondary", () => runAdvancedAction(torrent.hash, "recheck", {}, "Revérification demandée.")),
    button("Nouvelle annonce", "secondary", () => runAdvancedAction(torrent.hash, "reannounce", {}, "Nouvelle annonce demandée.")),
    button(
      torrent.sequentialDownload ? "Séquentiel normal" : "Téléchargement séquentiel",
      torrent.sequentialDownload ? "primary" : "secondary",
      () => runAdvancedAction(
        torrent.hash,
        "set-sequential",
        { enabled: !torrent.sequentialDownload },
        torrent.sequentialDownload ? "Téléchargement séquentiel désactivé." : "Téléchargement séquentiel activé.",
      ),
    ),
  );

  const form = document.createElement("form");
  form.className = "advanced-form";
  form.innerHTML = `
    <label>Catégorie
      <input name="category" value="${String(torrent.category || "").replace(/"/g, "&quot;")}">
    </label>
    <label>Tags
      <input name="tags" value="${String(torrent.tags || "").replace(/"/g, "&quot;")}">
    </label>
    <label>Limite DL (KiB/s)
      <input name="downloadLimit" type="number" min="0" value="${Math.max(0, Math.floor((Number(torrent.downloadLimit) || 0) / 1024))}">
    </label>
    <label>Limite UL (KiB/s)
      <input name="uploadLimit" type="number" min="0" value="${Math.max(0, Math.floor((Number(torrent.uploadLimit) || 0) / 1024))}">
    </label>
    <button type="submit" class="button primary">Appliquer</button>
  `;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    await runAdvancedAction(torrent.hash, "set-category", { category: String(data.get("category") || "") }, "Catégorie mise à jour.");
    if (String(data.get("tags") || "").trim()) {
      await runAdvancedAction(torrent.hash, "add-tags", { tags: String(data.get("tags") || "") }, "Tags mis à jour.");
    }
    await runAdvancedAction(
      torrent.hash,
      "set-download-limit",
      { limitKiB: Math.max(0, Number(data.get("downloadLimit") || 0)) },
      "Limite de téléchargement mise à jour.",
    );
    await runAdvancedAction(
      torrent.hash,
      "set-upload-limit",
      { limitKiB: Math.max(0, Number(data.get("uploadLimit") || 0)) },
      "Limite d'envoi mise à jour.",
    );
  });
  advanced.append(title, actions, form);

  const trackerSection = document.createElement("section");
  trackerSection.className = "detail-advanced";
  const tTitle = document.createElement("h3");
  tTitle.textContent = "Trackers";
  const tBody = document.createElement("div");
  tBody.className = "tracker-list";
  tBody.textContent = "Chargement…";
  trackerSection.append(tTitle, tBody);

  els.detailBody.replaceChildren(dl, trackerSection, advanced);

  const currentHash = torrent.hash;
  api(route(`/api/torrents/${currentHash}/trackers`), { cache: "no-store" }).then((data) => {
    if (state.detailHash !== currentHash) return;
    const trackers = Array.isArray(data.trackers) ? data.trackers : [];
    const filtered = trackers.filter((tr) => {
      const url = String(tr.url || "");
      return !url.startsWith("** [DHT]") && !url.startsWith("** [PeX]") && !url.startsWith("** [LSD]") && !url.startsWith("** [Metadata]");
    });
    if (!filtered.length) {
      tBody.textContent = "Aucun tracker.";
      return;
    }
    const list = document.createElement("div");
    list.className = "tracker-grid";
    for (const tr of filtered) {
      const row = document.createElement("div");
      row.className = "tracker-row";
      const statusClass = ["disabled", "not_contacted", "working", "updating", "not_working"][Number(tr.status)] || "unknown";
      const statusLabels = { disabled: "Désactivé", not_contacted: "En attente", working: "Opérationnel", updating: "Mise à jour", not_working: "Erreur", unknown: "Inconnu" };
      row.innerHTML = `
        <div class="tracker-url">${String(tr.url || "—").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</div>
        <div class="tracker-meta">
          <span class="tracker-status tracker-status-${statusClass}">${statusLabels[statusClass] || "Inconnu"}</span>
          ${Number(tr.num_seeds) > 0 ? `<span>${Number(tr.num_seeds)} S</span>` : ""}
          ${Number(tr.num_leeches) > 0 ? `<span>${Number(tr.num_leeches)} L</span>` : ""}
          ${tr.msg ? `<span class="tracker-msg">${String(tr.msg).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</span>` : ""}
        </div>`;
      list.append(row);
    }
    tBody.replaceChildren(list);
  }).catch(() => {
    tBody.textContent = "Erreur de chargement.";
  });
}

function openAddTrackerDialog() {
  const selectedHashes = [...state.selected];
  if (!selectedHashes.length) {
    showToast("Aucun torrent sélectionné.");
    return;
  }
  state.lastFocus = document.activeElement;
  const visible = filteredTorrents();
  const count = selectedHashes.filter((h) => visible.some((t) => t.hash === h)).length;
  els.addTrackerTitle.textContent = "Ajouter un tracker";
  els.addTrackerCount.textContent = `Ajouter un tracker à ${selectedHashes.length} torrent${selectedHashes.length > 1 ? "s" : ""}${count !== selectedHashes.length ? ` (dont ${count} dans les résultats affichés)` : ""}`;
  els.addTrackerUrlInput.value = "";
  els.addTrackerMessage.textContent = "";
  els.confirmAddTrackerButton.disabled = false;
  els.confirmAddTrackerButton.textContent = `Ajouter aux ${selectedHashes.length} torrent${selectedHashes.length > 1 ? "s" : ""}`;
  if (typeof trapFocus === "function") trapFocus(els.addTrackerDialog);
  els.addTrackerDialog.showModal();
}

async function confirmAddTracker() {
  const selectedHashes = [...state.selected];
  if (!selectedHashes.length) return;
  const trackerUrl = els.addTrackerUrlInput.value.trim();
  if (!trackerUrl) {
    els.addTrackerMessage.textContent = "Entrez une adresse de tracker.";
    els.addTrackerUrlInput.focus();
    return;
  }
  els.confirmAddTrackerButton.disabled = true;
  els.confirmAddTrackerButton.textContent = "Ajout…";
  els.addTrackerMessage.textContent = "";
  try {
    const payload = await api(route("/api/torrents/add-tracker"), {
      method: "POST",
      body: JSON.stringify({ hashes: selectedHashes, trackerUrl }),
    });
    const parts = [];
    if (payload.updated) parts.push(`${payload.updated} configuré${payload.updated > 1 ? "s" : ""}`);
    if (payload.duplicates) parts.push(`${payload.duplicates} déjà présent${payload.duplicates > 1 ? "s" : ""}`);
    if (payload.missing) parts.push(`${payload.missing} introuvable${payload.missing > 1 ? "s" : ""}`);
    if (payload.failed) parts.push(`${payload.failed} échec${payload.failed > 1 ? "s" : ""}`);
    showToast(`Tracker configuré sur ${payload.updated} torrent${payload.updated > 1 ? "s" : ""}${parts.length ? ` (${parts.join(", ")})` : ""}.`);
    els.addTrackerDialog.close();
    await loadTorrents({ silent: true, force: true });
    loadTrackerIndex();
  } catch (error) {
    els.addTrackerMessage.textContent = describeError(error);
  } finally {
    els.confirmAddTrackerButton.disabled = false;
    els.confirmAddTrackerButton.textContent = "Ajouter";
  }
}

function cancelAddTracker() {
  els.addTrackerDialog.close();
}

function configureLinks() {
  if (els.homeNavLink) els.homeNavLink.href = `${route("/")}?view=home`;
  if (els.activityNavLink) els.activityNavLink.href = "/activity/";
  if (els.torrentsNavLink) els.torrentsNavLink.href = `${route("/")}?view=torrents`;
  if (els.prowlarrNavLink) els.prowlarrNavLink.href = `${state.prowlarrPanelPrefix || "/prowlarr-panel"}/`;
  if (els.storageNavLink) els.storageNavLink.href = "/storage-panel/";
  if (els.mediaNavLink) els.mediaNavLink.href = "/media-panel/";
  if (els.healthNavLink) els.healthNavLink.href = "/health/";
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
    const payload = await api(route("/api/torrents/add"), {
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
  state.prefs = { ...state.prefs, search: "", status: "all", category: "all", tag: "all", tracker: "all", sort: "default", direction: "asc" };
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
  els.addTorrentButton?.addEventListener("click", openAddPanel);
  els.retryButton.addEventListener("click", () => loadTorrents({ force: true }));
  els.searchInput.addEventListener("input", () => updatePreference("search", els.searchInput.value));
  els.statusFilter.addEventListener("change", () => updatePreference("status", els.statusFilter.value));
  els.categoryFilter.addEventListener("change", () => updatePreference("category", els.categoryFilter.value));
  els.tagFilter.addEventListener("change", () => updatePreference("tag", els.tagFilter.value));
  els.trackerFilter.addEventListener("change", () => updatePreference("tracker", els.trackerFilter.value));
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
  els.bulkAddTracker.addEventListener("click", openAddTrackerDialog);
  els.bulkDelete.addEventListener("click", (event) => {
    const selectedTorrents = state.torrents.filter((torrent) => state.selected.has(torrent.hash));
    openDeleteDialog(selectedTorrents, event.currentTarget);
  });
  els.confirmAddTrackerButton.addEventListener("click", confirmAddTracker);
  els.cancelAddTrackerButton.addEventListener("click", cancelAddTracker);
  els.addTrackerDialog.addEventListener("close", () => state.lastFocus?.focus?.());
  els.addTrackerUrlInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      confirmAddTracker();
    }
  });
  document.querySelector("#addTrackerForm")?.addEventListener("submit", (event) => {
    event.preventDefault();
    confirmAddTracker();
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
  configureLinks();
  bindEvents();
  applyUrlState();
  renderControls();
  restartRefreshTimer();
  try {
    await refreshSession();
    await loadTorrents({ force: true });
    loadTrackerIndex();
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
