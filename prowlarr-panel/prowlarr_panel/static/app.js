const config = window.__PROWLARR_PANEL_CONFIG__ || {};
const state = {
  publicPrefix: String(config.publicPrefix || "").replace(/\/$/, ""),
  prowlarrPanelPrefix: String(config.prowlarrPanelPrefix || config.publicPrefix || "/prowlarr-panel").replace(/\/$/, ""),
  torrentPanelPrefix: String(config.torrentPanelPrefix || "/torrent-panel").replace(/\/$/, ""),
  cloudPanelPrefix: String(config.cloudPanelPrefix || "/cloud-panel").replace(/\/$/, ""),
  activityPrefix: String(config.activityPrefix || "/activity").replace(/\/$/, ""),
  storagePrefix: String(config.storagePrefix || "/storage-panel").replace(/\/$/, ""),
  mediaPrefix: String(config.mediaPrefix || "/media-panel").replace(/\/$/, ""),
  healthPrefix: String(config.healthPrefix || "/health").replace(/\/$/, ""),
  csrfToken: "",
  indexers: [],
  results: [],
  applications: [],
  alerts: [],
  events: [],
  capabilities: {},
  busy: new Set(),
  busyLabels: new Map(),
  activeView: "indexers",
  searchAbort: null,
  searchRequestId: 0,
  searchInFlight: false,
  searchPromise: null,
  loadPromise: null,
  queuedRefreshPromise: null,
  pollTimer: null,
  freshnessTimer: null,
  live: {
    controller: null,
    topics: "",
    connected: false,
  },
  sectionUpdatedAt: {},
  sectionSignatures: {},
  initialized: false,
  resultPage: 0,
  indexerRowNodes: new Map(),
  resultRowNodes: new Map(),
  releaseIndexerSelection: new Set(),
  sectionErrors: {},
  sectionPromises: {},
  actionRevision: 0,
  actionInFlight: 0,
  partialFailures: [],
  lastGrab: null,
  sectionLoaded: {
    overview: false,
    indexers: false,
    applications: false,
    health: false,
    history: false,
  },
};

const RESULT_PAGE_SIZE = 80;
const POLL_INTERVAL_MS = 1000;
const LIVE_STALE_AFTER_MS = 15000;

const SECTION_ENDPOINTS = {
  overview: "/api/overview",
  indexers: "/api/indexers",
  applications: "/api/applications",
  health: "/api/health",
  history: "/api/history",
};

const VIEW_SECTIONS = {
  indexers: ["overview", "indexers"],
  search: ["overview", "indexers"],
  applications: ["overview", "applications"],
  health: ["overview", "health", "history"],
};

const SEARCH_CATEGORY_PRESETS = [
  { label: "Films", value: 2000 },
  { label: "Series", value: 5000 },
  { label: "Musique", value: 3000 },
  { label: "Livres", value: 7000 },
  { label: "Autres", value: 8000 },
];

const els = {
  refreshStatus: document.querySelector("#refreshStatus"),
  lastCheck: document.querySelector("#lastCheck"),
  refreshButton: document.querySelector("#refreshButton"),
  retryButton: document.querySelector("#retryButton"),
  alert: document.querySelector("#alert"),
  alertText: document.querySelector("#alertText"),
  summaryGrid: document.querySelector("#summaryGrid"),
  pageStatus: document.querySelector("#pageStatus"),
  statusText: document.querySelector("#statusText"),
  sidebarStatusDetail: document.querySelector("#sidebarStatusDetail"),
  homeLink: document.querySelector("#homeLink"),
  activityLink: document.querySelector("#activityLink"),
  torrentLink: document.querySelector("#torrentLink"),
  prowlarrLink: document.querySelector("#prowlarrLink"),
  cloudLink: document.querySelector("#cloudLink"),
  storageLink: document.querySelector("#storageLink"),
  mediaLink: document.querySelector("#mediaLink"),
  healthLink: document.querySelector("#healthLink"),
  tabs: [...document.querySelectorAll(".tab")],
  views: {
    indexers: document.querySelector("#indexersView"),
    search: document.querySelector("#searchView"),
    applications: document.querySelector("#applicationsView"),
    health: document.querySelector("#healthView"),
  },
  indexersSummary: document.querySelector("#indexersSummary"),
  indexersError: document.querySelector("#indexersError"),
  indexerRows: document.querySelector("#indexerRows"),
  indexersEmpty: document.querySelector("#indexersEmpty"),
  indexerSearch: document.querySelector("#indexerSearch"),
  indexerState: document.querySelector("#indexerState"),
  indexerProtocol: document.querySelector("#indexerProtocol"),
  indexerTag: document.querySelector("#indexerTag"),
  indexerSort: document.querySelector("#indexerSort"),
  resetIndexers: document.querySelector("#resetIndexers"),
  testAllButton: document.querySelector("#testAllButton"),
  searchForm: document.querySelector("#searchForm"),
  releaseQuery: document.querySelector("#releaseQuery"),
  releaseCategories: document.querySelector("#releaseCategories"),
  releaseCategoryChoices: document.querySelector("#releaseCategoryChoices"),
  releaseIndexerSearch: document.querySelector("#releaseIndexerSearch"),
  releaseIndexers: document.querySelector("#releaseIndexers"),
  selectAllIndexers: document.querySelector("#selectAllIndexers"),
  clearIndexers: document.querySelector("#clearIndexers"),
  searchButton: document.querySelector("#searchButton"),
  searchSummary: document.querySelector("#searchSummary"),
  searchPartialFailures: document.querySelector("#searchPartialFailures"),
  grabNotice: document.querySelector("#grabNotice"),
  resultRows: document.querySelector("#resultRows"),
  resultsEmpty: document.querySelector("#resultsEmpty"),
  resultSearch: document.querySelector("#resultSearch"),
  resultIndexer: document.querySelector("#resultIndexer"),
  resultProtocol: document.querySelector("#resultProtocol"),
  resultCategory: document.querySelector("#resultCategory"),
  resultMaxSize: document.querySelector("#resultMaxSize"),
  resultSort: document.querySelector("#resultSort"),
  resultPagination: document.querySelector("#resultPagination"),
  resultRange: document.querySelector("#resultRange"),
  resultPrevious: document.querySelector("#resultPrevious"),
  resultNext: document.querySelector("#resultNext"),
  appsGrid: document.querySelector("#appsGrid"),
  appsSummary: document.querySelector("#appsSummary"),
  appsError: document.querySelector("#appsError"),
  alertsList: document.querySelector("#alertsList"),
  historyList: document.querySelector("#historyList"),
  healthSummary: document.querySelector("#healthSummary"),
  healthError: document.querySelector("#healthError"),
  healthTab: document.querySelector("#healthTab"),
  confirmDialog: document.querySelector("#confirmDialog"),
  confirmTitle: document.querySelector("#confirmTitle"),
  confirmText: document.querySelector("#confirmText"),
  cancelConfirm: document.querySelector("#cancelConfirm"),
  acceptConfirm: document.querySelector("#acceptConfirm"),
  toast: document.querySelector("#toast"),
};

function currentUrl() {
  return new URL(window.location?.href || "http://localhost/");
}

function prefixed(path = "/") {
  const root = state.publicPrefix || "";
  if (path === "/") return `${root || ""}/`;
  return `${root}${path.startsWith("/") ? path : `/${path}`}`;
}

function text(value, fallback = "—") {
  const cleaned = String(value ?? "").trim();
  return cleaned || fallback;
}

function clearNode(node) {
  node.replaceChildren();
}

const { element, button, state: systemState } = window.DashboardDOM;

function parseTimestamp(value) {
  const parsed = value ? new Date(value) : new Date();
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function setRefreshStamp(value = null) {
  const now = parseTimestamp(typeof value === "string" ? value : null);
  if (els.lastCheck) {
    els.lastCheck.textContent = now.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    els.lastCheck.dateTime = now.toISOString();
  }
}

function noteFreshness(sections, updatedAt) {
  if (!updatedAt) return;
  const names = Array.isArray(sections) ? sections : [sections];
  let newest = null;
  names.filter(Boolean).forEach((name) => {
    const current = Date.parse(state.sectionUpdatedAt[name] || "");
    const incoming = Date.parse(updatedAt);
    if (Number.isFinite(current) && Number.isFinite(incoming) && incoming < current) return;
    state.sectionUpdatedAt[name] = updatedAt;
    if (!newest || String(updatedAt) > newest) newest = String(updatedAt);
  });
  if (newest) setRefreshStamp(newest);
  refreshFreshnessIndicator();
}

function refreshFreshnessIndicator() {
  if (!els.refreshStatus) return;
  const staleSections = Object.entries(state.sectionUpdatedAt)
    .filter(([, value]) => Date.now() - parseTimestamp(value).getTime() > LIVE_STALE_AFTER_MS)
    .map(([name]) => name);
  els.refreshStatus.classList?.toggle("stale", staleSections.length > 0);
  els.refreshStatus.title = staleSections.length
    ? `Données anciennes: ${staleSections.join(", ")}`
    : "Dernière collecte réussie";
}

function setSidebarStatus(label, detail, tone = "operational") {
  if (els.statusText) els.statusText.textContent = label;
  if (els.sidebarStatusDetail) els.sidebarStatusDetail.textContent = detail;
  const dot = document.querySelector(".sidebar-health .status-dot");
  if (dot?.classList) {
    dot.classList.remove("degraded", "checking", "unavailable");
    if (tone === "degraded") dot.classList.add("degraded");
    if (tone === "checking") dot.classList.add("checking");
    if (tone === "unavailable") dot.classList.add("unavailable");
  }
}

function setPageStatus(message, tone = "info") {
  if (!els.pageStatus) return;
  els.pageStatus.hidden = !message;
  els.pageStatus.textContent = message || "";
  els.pageStatus.className = `page-status ${tone}`.trim();
}

function normalizedTrackingQuery(title) {
  return String(title || "")
    .replace(/\[[^\]]+\]/g, " ")
    .replace(/[._]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);
}

function isTr4kerIndexer(indexer) {
  return String(indexer || "").toLocaleLowerCase("fr").includes("tr4ker");
}

function buildTorrentFollowUrl(title, indexer = "") {
  const query = normalizedTrackingQuery(title);
  const root = `${state.torrentPanelPrefix || "/torrent-panel"}/`;
  const url = new URL(root, currentUrl());
  url.searchParams.set("view", "torrents");
  if (query) url.searchParams.set("search", query);
  url.searchParams.set("refresh", "1");
  url.searchParams.set("source", isTr4kerIndexer(indexer) ? "tr4ker" : "prowlarr");
  if (title) url.searchParams.set("pendingRelease", title);
  return `${url.pathname}${url.search}`;
}

function clearError() {
  els.alert.hidden = true;
  els.alertText.textContent = "";
}

const showToast = createToast(() => els.toast);
const showError = (error) => showErrorMessage(els.alert, error, { messageElement: els.alertText, recovery: "Reessayer" });

function route(path) {
  return prefixed(path);
}

const prowlarrApiClient = createApiClient({
  csrfHeader: "X-Prowlarr-Panel-CSRF",
  getCsrfToken: () => state.csrfToken,
  setCsrfToken: (token) => { state.csrfToken = token; },
  sessionPath: route("/api/session"),
});
const api = prowlarrApiClient.request;
const refreshSession = prowlarrApiClient.refreshSession;

function applyBusyState(node, key, defaultLabel) {
  const busy = state.busy.has(key);
  node.disabled = busy;
  node.setAttribute("aria-busy", String(busy));
  node.textContent = busy ? (state.busyLabels.get(key) || "En cours...") : defaultLabel;
}

function setBusy(key, busy, label = "En cours...") {
  if (busy) {
    state.busy.add(key);
    state.busyLabels.set(key, label);
  } else {
    state.busy.delete(key);
    state.busyLabels.delete(key);
  }
  document.querySelectorAll(`[data-busy-key="${CSS.escape(key)}"]`).forEach((node) => {
    applyBusyState(node, key, node.dataset.label || String(node.textContent || ""));
  });
}

function fillSelect(select, entries, current = "all") {
  const previous = select.value;
  const unchanged = select.options?.length === entries.length && entries.every(
    ([value, label], index) => select.options[index]?.value === String(value) && select.options[index]?.textContent === String(label),
  );
  if (unchanged) {
    const available = entries.some(([value]) => String(value) === previous);
    select.value = available ? previous : String(current);
    return;
  }
  const options = entries.map(([value, label]) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(label);
    return option;
  });
  select.replaceChildren(...options);
  const available = entries.some(([value]) => String(value) === previous);
  select.value = available ? previous : String(current);
}

function renderSummary(overview) {
  const stats = [
    ["Connexion", overview.connection === "ready" ? "OK" : "Erreur"],
    ["Actifs", overview.indexersActive],
    ["En erreur", overview.indexersError],
  ];
  els.summaryGrid.replaceChildren(
    ...stats.map(([label, value]) =>
      element("article", {
        className: "stat",
        children: [element("strong", { text: text(value, "0") }), element("span", { text: label })],
      }),
    ),
  );
  state.capabilities = overview.capabilities || {};
  setSidebarStatus(
    overview.connection === "ready" ? "Connexion prête" : "Connexion en erreur",
    `${overview.indexersActive || 0} indexeur(s) actif(s), ${overview.systemWarnings || 0} alerte(s).`,
    overview.connection === "ready" ? (overview.systemWarnings ? "degraded" : "operational") : "unavailable",
  );
  setPageStatus(
    overview.systemWarnings ? `${overview.systemWarnings} alerte(s) système à examiner.` : "Indexeurs, recherche et applications alignés.",
    overview.systemWarnings ? "warn" : "ok",
  );
  noteFreshness("overview", overview.refreshedAt || overview.lastSuccessfulRefresh);
}

function indexerHealthLabel(indexer) {
  if (indexer.health === "error") return "Erreur";
  if (!indexer.enabled) return "Desactive";
  return "Actif";
}

function renderSectionError(section, error) {
  state.sectionErrors[section] = error || null;
  const group = section === "history" ? "health" : section;
  const container = els[`${group}Error`];
  if (!container) return;
  const groupError = group === "health"
    ? (state.sectionErrors.health || state.sectionErrors.history)
    : state.sectionErrors[group];
  const message = container.querySelector("span");
  message.textContent = groupError ? describeError(groupError) : "";
  container.hidden = !groupError;
}

function renderIndexerFilters() {
  const protocols = [...new Set(state.indexers.map((item) => item.protocol).filter(Boolean))].sort();
  const tags = [...new Set(state.indexers.flatMap((item) => item.tags || []))].sort((a, b) => String(a).localeCompare(String(b)));
  fillSelect(els.indexerState, [["all", "Tous"], ["active", "Actifs"], ["disabled", "Desactives"], ["error", "En erreur"]], els.indexerState.value || "all");
  fillSelect(els.indexerProtocol, [["all", "Tous"], ...protocols.map((item) => [item, item])], els.indexerProtocol.value || "all");
  fillSelect(els.indexerTag, [["all", "Tous"], ...tags.map((item) => [item, item])], els.indexerTag.value || "all");
  fillSelect(els.indexerSort, [["name", "Nom"], ["priority", "Priorite"], ["state", "Etat"]], els.indexerSort.value || "name");
}

function filteredIndexers() {
  const query = els.indexerSearch.value.trim().toLowerCase();
  const stateFilter = els.indexerState.value;
  const protocol = els.indexerProtocol.value;
  const tag = els.indexerTag.value;
  const sort = els.indexerSort.value;
  return [...state.indexers]
    .filter((item) => !query || String(item.name || "").toLowerCase().includes(query))
    .filter((item) => stateFilter === "all" || (stateFilter === "active" && item.enabled && item.health !== "error") || (stateFilter === "disabled" && !item.enabled) || (stateFilter === "error" && item.health === "error"))
    .filter((item) => protocol === "all" || item.protocol === protocol)
    .filter((item) => tag === "all" || (item.tags || []).map(String).includes(tag))
    .sort((a, b) => {
      if (sort === "priority") return Number(a.priority || 0) - Number(b.priority || 0);
      if (sort === "state") return indexerHealthLabel(a).localeCompare(indexerHealthLabel(b));
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
}

function removeDomNode(node) {
  if (typeof node?.remove === "function") {
    node.remove();
  } else if (node?.parentNode?.removeChild) {
    node.parentNode.removeChild(node);
  }
}

function insertDomNode(container, node, reference = null) {
  if (typeof container.insertBefore === "function") {
    container.insertBefore(node, reference);
  } else {
    container.appendChild(node);
  }
}

function rowKey(prefix, item, index, seen) {
  const rawIdentity = item?.id || item?.guid || `${item?.indexer || "item"}:${item?.title || item?.name || index}`;
  const base = `${prefix}:${String(rawIdentity)}`;
  let key = base;
  let suffix = 1;
  while (seen.has(key)) {
    key = `${base}#${suffix}`;
    suffix += 1;
  }
  seen.add(key);
  return key;
}

function syncKeyedRows(container, items, prefix, cache, createRow, updateRow) {
  const activeBeforeRender = document.activeElement;
  const orderedRows = [];
  const seen = new Set();
  items.forEach((item, index) => {
    const key = rowKey(prefix, item, index, seen);
    let row = cache.get(key);
    if (!row) {
      row = createRow(item, key);
      row.className = "row-enter";
      cache.set(key, row);
    }
    row.dataset.rowKey = key;
    updateRow(row, item, key);
    orderedRows.push(row);
  });

  [...cache.entries()].forEach(([key, row]) => {
    if (seen.has(key)) return;
    removeDomNode(row);
    cache.delete(key);
  });

  orderedRows.forEach((row, index) => {
    const current = container.childNodes[index];
    if (current !== row) insertDomNode(container, row, current || null);
  });
  while (container.childNodes.length > orderedRows.length) {
    removeDomNode(container.childNodes[container.childNodes.length - 1]);
  }
  if (activeBeforeRender && activeBeforeRender.isConnected === false) {
    const fallback = prefix === "indexer" ? els.indexerSearch : els.resultSearch;
    fallback?.focus();
  }
}

function createIndexerRow(item, key) {
  const row = element("tr");
  ["Nom", "Protocole", "Etat", "Priorite", "Tags", "Categories", "Dernier test"].forEach((label) => {
    row.append(element("td", { attrs: { "data-label": label } }));
  });
  const testButton = button("Tester", "button secondary", {
    action: "test-indexer",
    busyKey: `test:${item.id}`,
    label: "Tester",
  });
  const toggleButton = button("Activer", "button secondary", {
    action: "toggle-indexer",
    busyKey: `toggle:${item.id}`,
    label: "Activer",
  });
  row.appendChild(element("td", {
    attrs: { "data-label": "Actions" },
    children: [element("div", { className: "action-row", children: [testButton, toggleButton] })],
  }));
  row.indexerControls = { testButton, toggleButton };
  row.dataset.rowKey = key;
  return row;
}

function updateIndexerRow(row, item) {
  const cells = row.children;
  cells[0].className = "name-cell";
  cells[0].setAttribute("data-label", "Nom");
  cells[0].textContent = text(item.name);
  cells[1].setAttribute("data-label", "Protocole");
  cells[1].textContent = text(item.protocol);
  cells[2].replaceChildren(
    element("span", {
      className: `badge ${item.health === "error" ? "error" : item.enabled ? "ok" : "disabled"}`,
      text: indexerHealthLabel(item),
    }),
    ...(item.error ? [element("div", { className: "muted", text: text(item.error) })] : []),
  );
  cells[2].setAttribute("data-label", "Etat");
  cells[3].className = "mono";
  cells[3].setAttribute("data-label", "Priorite");
  cells[3].textContent = text(item.priority, "0");
  cells[4].setAttribute("data-label", "Tags");
  const tags = (item.tags || []).map((tag) => element("span", { className: "badge", text: String(tag) }));
  cells[4].replaceChildren(...(tags.length ? tags : [document.createTextNode("—")]));
  cells[5].setAttribute("data-label", "Categories");
  cells[5].textContent = Array.isArray(item.categories) ? String(item.categories.length) : "—";
  cells[6].setAttribute("data-label", "Dernier test");
  cells[6].textContent = text(item.lastTest);
  cells[7].setAttribute("data-label", "Actions");

  const { testButton, toggleButton } = row.indexerControls;
  const toggleLabel = item.enabled ? "Desactiver" : "Activer";
  testButton.dataset.id = String(item.id);
  testButton.dataset.label = "Tester";
  applyBusyState(testButton, `test:${item.id}`, "Tester");
  toggleButton.dataset.id = String(item.id);
  toggleButton.dataset.enabled = String(Boolean(item.enabled));
  toggleButton.dataset.name = text(item.name);
  toggleButton.dataset.label = toggleLabel;
  toggleButton.className = `button ${item.enabled ? "danger" : "secondary"}`;
  applyBusyState(toggleButton, `toggle:${item.id}`, toggleLabel);
}

function renderIndexers() {
  renderIndexerFilters();
  const rows = filteredIndexers();
  els.indexersSummary.textContent = state.sectionErrors.indexers
    ? `${rows.length} affiche(s) sur ${state.indexers.length}. Derniere donnees conservees.`
    : `${rows.length} affiche(s) sur ${state.indexers.length}.`;
  els.indexersEmpty.hidden = rows.length > 0;
  renderReleaseIndexers();
  syncKeyedRows(els.indexerRows, rows, "indexer", state.indexerRowNodes, createIndexerRow, updateIndexerRow);
}

function renderReleaseCategories() {
  els.releaseCategoryChoices.replaceChildren(
    ...SEARCH_CATEGORY_PRESETS.map((item) =>
      element("label", {
        className: "choice-pill",
        children: [
          element("input", { attrs: { type: "checkbox", value: item.value } }),
          element("span", { text: item.label }),
        ],
      }),
    ),
  );
}

function selectedCategoryIds() {
  const presetValues = [...els.releaseCategoryChoices.querySelectorAll('input[type="checkbox"]:checked')]
    .map((input) => Number(input.value))
    .filter(Number.isInteger);
  const advancedValues = els.releaseCategories.value.split(",").map((item) => Number(item.trim())).filter((value) => Number.isInteger(value) && value > 0);
  return [...new Set([...presetValues, ...advancedValues])];
}

function renderReleaseIndexers() {
  const validIds = new Set(state.indexers.map((item) => String(item.id)));
  [...state.releaseIndexerSelection].forEach((id) => {
    if (!validIds.has(String(id))) state.releaseIndexerSelection.delete(id);
  });
  const query = els.releaseIndexerSearch.value.trim().toLowerCase();
  const entries = state.indexers
    .filter((item) => !query || String(item.name || "").toLowerCase().includes(query))
    .map((item) => [String(item.id), text(item.name)]);
  const signature = JSON.stringify(entries);
  if (els.releaseIndexers.dataset.optionsSignature !== signature) {
    els.releaseIndexers.replaceChildren(...entries.map(([value, label]) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      return option;
    }));
    els.releaseIndexers.dataset.optionsSignature = signature;
  }
  [...els.releaseIndexers.options].forEach((option) => {
    option.selected = state.releaseIndexerSelection.has(option.value);
  });
}

function filteredResults() {
  const query = els.resultSearch.value.trim().toLowerCase();
  const indexer = els.resultIndexer.value;
  const protocol = els.resultProtocol.value;
  const category = els.resultCategory.value.trim().toLowerCase();
  const maxSize = Number(els.resultMaxSize.value || 0) * 1024 ** 3;
  const sort = els.resultSort.value || "seeders";
  return [...state.results]
    .filter((item) => !query || String(item.title || "").toLowerCase().includes(query))
    .filter((item) => indexer === "all" || item.indexer === indexer)
    .filter((item) => protocol === "all" || item.protocol === protocol)
    .filter((item) => !category || String(item.category || "").toLowerCase().includes(category))
    .filter((item) => !maxSize || Number(item.size || 0) <= maxSize)
    .sort((a, b) => {
      if (sort === "size") return Number(b.size || 0) - Number(a.size || 0);
      if (sort === "age") return String(a.age || "").localeCompare(String(b.age || ""));
      if (sort === "indexer") return String(a.indexer || "").localeCompare(String(b.indexer || ""));
      return Number(b.seeders || 0) - Number(a.seeders || 0);
    });
}

function renderResultFilters() {
  const indexers = [...new Set(state.results.map((item) => item.indexer).filter(Boolean))].sort();
  const protocols = [...new Set(state.results.map((item) => item.protocol).filter(Boolean))].sort();
  fillSelect(els.resultIndexer, [["all", "Tous"], ...indexers.map((item) => [item, item])], els.resultIndexer.value || "all");
  fillSelect(els.resultProtocol, [["all", "Tous"], ...protocols.map((item) => [item, item])], els.resultProtocol.value || "all");
  fillSelect(els.resultSort, [["seeders", "Seeders"], ["size", "Taille"], ["age", "Age"], ["indexer", "Indexer"]], els.resultSort.value || "seeders");
}

function renderResults() {
  renderResultFilters();
  const rows = filteredResults();
  const pageCount = Math.max(1, Math.ceil(rows.length / RESULT_PAGE_SIZE));
  state.resultPage = Math.min(Math.max(0, state.resultPage), pageCount - 1);
  const pageStart = state.resultPage * RESULT_PAGE_SIZE;
  const visibleRows = rows.slice(pageStart, pageStart + RESULT_PAGE_SIZE);
  const partialCount = state.partialFailures.length;
  const countLabel = rows.length > RESULT_PAGE_SIZE
    ? `${pageStart + 1}-${pageStart + visibleRows.length} resultat(s) affiche(s) sur ${rows.length}`
    : `${rows.length} resultat(s) affiche(s)`;
  els.searchSummary.textContent = partialCount > 0
    ? `${countLabel}. ${partialCount} source(s) ont repondu avec une erreur partielle.`
    : `${countLabel}. Les URLs privees restent cote serveur.`;
  if (els.searchPartialFailures) {
    if (partialCount > 0) {
      els.searchPartialFailures.hidden = false;
      els.searchPartialFailures.textContent = state.partialFailures
        .map((item) => text(item.message || item.error || item.indexer || "Erreur partielle"))
        .join(" ");
    } else {
      els.searchPartialFailures.hidden = true;
      els.searchPartialFailures.textContent = "";
    }
  }
  els.resultsEmpty.hidden = rows.length > 0;
  syncKeyedRows(els.resultRows, visibleRows, "result", state.resultRowNodes, createResultRow, updateResultRow);
  if (els.resultPagination) {
    els.resultPagination.hidden = rows.length <= RESULT_PAGE_SIZE;
  }
  if (els.resultRange) {
    els.resultRange.textContent = rows.length > RESULT_PAGE_SIZE
      ? `Page ${state.resultPage + 1} sur ${pageCount}`
      : "";
  }
  if (els.resultPrevious) {
    els.resultPrevious.disabled = state.resultPage === 0;
  }
  if (els.resultNext) {
    els.resultNext.disabled = state.resultPage >= pageCount - 1;
  }
}

function createResultRow(item, key) {
  const row = element("tr");
  ["Titre", "Indexer", "Categorie", "Taille", "Age", "Seeders", "Leechers", "Protocole", "Statut", "Action"].forEach((label) => {
    row.append(element("td", { attrs: { "data-label": label } }));
  });
  const actionCell = row.children[9];
  const grabButton = button("Envoyer vers qBittorrent", "button primary", {
    action: "grab",
    busyKey: `grab:${item.id}`,
    label: "Envoyer vers qBittorrent",
  });
  actionCell.append(grabButton);
  row.grabButton = grabButton;
  row.dataset.rowKey = key;
  return row;
}

function updateResultRow(row, item) {
  const cells = row.children;
  cells[0].className = "name-cell";
  cells[0].setAttribute("data-label", "Titre");
  cells[0].textContent = text(item.title);
  cells[1].setAttribute("data-label", "Indexer");
  cells[1].textContent = text(item.indexer);
  cells[2].setAttribute("data-label", "Categorie");
  cells[2].textContent = text(item.category);
  cells[3].className = "mono";
  cells[3].setAttribute("data-label", "Taille");
  cells[3].textContent = formatBytes(item.size);
  cells[4].setAttribute("data-label", "Age");
  cells[4].textContent = text(item.age);
  cells[5].className = "mono";
  cells[5].setAttribute("data-label", "Seeders");
  cells[5].textContent = text(item.seeders, "0");
  cells[6].className = "mono";
  cells[6].setAttribute("data-label", "Leechers");
  cells[6].textContent = text(item.leechers, "0");
  cells[7].setAttribute("data-label", "Protocole");
  cells[7].textContent = text(item.protocol);
  cells[8].setAttribute("data-label", "Statut");
  cells[8].textContent = item.freeleech ? "Freeleech" : `DL ${text(item.downloadFactor)} / UL ${text(item.uploadFactor)}`;
  cells[9].setAttribute("data-label", "Action");

  const grabButton = row.grabButton;
  grabButton.dataset.releaseId = String(item.id ?? "");
  grabButton.dataset.title = text(item.title);
  grabButton.dataset.indexer = text(item.indexer);
  grabButton.dataset.label = "Envoyer vers qBittorrent";
  applyBusyState(grabButton, `grab:${item.id}`, "Envoyer vers qBittorrent");
}

function scrollResultsToStart() {
  const resultsStart = els.resultRows?.closest?.(".table-scroll") || els.resultRows;
  if (!resultsStart) return;
  const rect = resultsStart.getBoundingClientRect?.();
  if (typeof window.scrollTo === "function" && Number.isFinite(rect?.top)) {
    const documentOffset = Number(window.scrollY || window.pageYOffset || 0);
    window.scrollTo({ top: Math.max(0, rect.top + documentOffset), behavior: "auto" });
    return;
  }
  resultsStart.scrollIntoView?.({ block: "start", behavior: "auto" });
}

function showResultPage(delta) {
  const pageCount = Math.max(1, Math.ceil(filteredResults().length / RESULT_PAGE_SIZE));
  const nextPage = Math.min(Math.max(0, state.resultPage + delta), pageCount - 1);
  if (nextPage === state.resultPage) return false;
  state.resultPage = nextPage;
  renderResults();
  scrollResultsToStart();
  return true;
}

function renderGrabNotice() {
  if (!els.grabNotice) return;
  if (!state.lastGrab) {
    els.grabNotice.hidden = true;
    els.grabNotice.replaceChildren();
    return;
  }
  const follow = element("a", {
    className: "button primary",
    text: "Suivre dans Torrent Panel",
    attrs: { href: state.lastGrab.followUrl },
  });
  const dismiss = button("Fermer", "button secondary", { action: "dismiss-grab-notice" });
  els.grabNotice.hidden = false;
  els.grabNotice.replaceChildren(
    element("strong", { text: `Envoye vers qBittorrent : ${state.lastGrab.title}` }),
    element("span", { text: "Prowlarr a accepte la demande. Le torrent peut prendre quelques secondes avant d'apparaitre." }),
    element("div", { className: "grab-actions", children: [follow, dismiss] }),
  );
}

function renderApplications() {
  els.appsSummary.textContent = state.sectionErrors.applications
    ? `${state.applications.length} application(s) configuree(s). Derniere donnees conservees.`
    : `${state.applications.length} application(s) configuree(s).`;
  const cards = state.applications.map((item) => {
    const dl = element("dl");
    const pairs = [
      ["Type", text(item.type)],
      ["Etat", item.enabled ? "Active" : "Desactivee"],
      ["Test", text(item.lastTest)],
      ["Sync", text(item.syncLevel)],
      ["Tags", (item.tags || []).join(", ") || "—"],
    ];
    pairs.forEach(([label, value], index) => {
      if (index === 1) {
        dl.append(
          element("dt", { text: label }),
          element("dd", { children: [element("span", { className: `badge ${item.enabled ? "ok" : "disabled"}`, text: value })] }),
        );
        return;
      }
      dl.append(element("dt", { text: label }), element("dd", { text: value }));
    });
    return element("article", {
      className: "card",
      children: [element("h3", { text: text(item.name) }), dl],
    });
  });
  if (!cards.length) {
    cards.push(systemState({
      type: "empty",
      title: "Aucune application",
      message: "Aucune application connectée n'est disponible pour le moment.",
      compact: true,
    }));
  }
  els.appsGrid.replaceChildren(...cards);
}

function renderHealth() {
  els.healthSummary.textContent = state.sectionErrors.health
    ? `${state.alerts.length} alerte(s), ${state.events.length} evenement(s). Donnees partielles conservees.`
    : `${state.alerts.length} alerte(s), ${state.events.length} evenement(s).`;
  const alerts = state.alerts.map((item) =>
    element("article", {
      className: "list-item",
      children: [
        element("strong", { text: text(item.source) }),
        element("span", { text: text(item.message) }),
        element("span", { className: "muted", text: `${text(item.type)} ${text(item.date, "")}`.trim() }),
      ],
    }),
  );
  const events = state.events.map((item) =>
    element("article", {
      className: "list-item",
      children: [
        element("strong", { text: text(item.title, item.eventType) }),
        element("span", { text: `${text(item.indexer)} ${text(item.result, "")}`.trim() }),
        element("span", { className: "muted", text: text(item.date) }),
      ],
    }),
  );
  els.alertsList.replaceChildren(...(alerts.length ? alerts : [systemState({
    type: "empty",
    title: "Aucune alerte",
    message: "La santé Prowlarr ne signale aucun problème système.",
    compact: true,
  })]));
  els.historyList.replaceChildren(...(events.length ? events : [systemState({
    type: "empty",
    title: "Aucun événement récent",
    message: "Les dernières opérations apparaîtront ici après synchronisation.",
    compact: true,
  })]));
  els.healthTab.textContent = state.alerts.length > 0 ? `Sante (${state.alerts.length})` : "Sante";
}

function updateUrl(replace = true) {
  const url = currentUrl();
  url.searchParams.set("view", state.activeView);
  const query = els.releaseQuery.value.trim();
  if (state.activeView === "search" && query) url.searchParams.set("query", query);
  else url.searchParams.delete("query");
  const method = replace ? "replaceState" : "pushState";
  window.history?.[method]?.({}, "", url.toString());
}

function focusActiveTab() {
  const active = els.tabs.find((tab) => tab.dataset.view === state.activeView);
  active?.focus();
}

function setView(view, options = {}) {
  state.activeView = view;
  Object.entries(els.views).forEach(([name, panel]) => {
    const active = name === view;
    panel.hidden = !active;
    panel.setAttribute("tabindex", active ? "0" : "-1");
  });
  els.tabs.forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.setAttribute("aria-selected", String(active));
    tab.setAttribute("tabindex", active ? "0" : "-1");
  });
  updateUrl(options.replace !== false);
  if (options.focus) focusActiveTab();
  if (state.initialized) {
    loadAll(false, { sections: VIEW_SECTIONS[view], background: true }).catch(() => null);
    connectLive();
  }
}

function applyUrlState() {
  const url = currentUrl();
  const requestedView = url.searchParams.get("view");
  state.activeView = Object.hasOwn(els.views, requestedView) ? requestedView : "indexers";
  const query = url.searchParams.get("query");
  if (query) els.releaseQuery.value = query;
}

function setRefreshBusy(busy) {
  [els.refreshButton, els.retryButton].forEach((node) => {
    if (!node) return;
    node.disabled = busy;
    node.setAttribute("aria-busy", String(busy));
  });
}

function sectionKeysFor(options, view = state.activeView) {
  const requested = Array.isArray(options?.sections) ? options.sections : (VIEW_SECTIONS[view] || VIEW_SECTIONS.indexers);
  return [...new Set(requested.filter((key) => Object.hasOwn(SECTION_ENDPOINTS, key)))];
}

function applySectionData(key, payload) {
  const knownAt = Date.parse(state.sectionUpdatedAt[key] || "");
  const incomingAt = Date.parse(payload?.refreshedAt || "");
  if (Number.isFinite(knownAt) && Number.isFinite(incomingAt) && incomingAt < knownAt) return false;
  state.sectionLoaded[key] = true;
  renderSectionError(key, null);
  const content = { ...(payload || {}) };
  delete content.refreshedAt;
  const signature = JSON.stringify(content);
  if (state.sectionSignatures[key] === signature) {
    if (payload?.refreshedAt) noteFreshness(key, payload.refreshedAt);
    return;
  }
  state.sectionSignatures[key] = signature;
  if (key === "overview") renderSummary(payload || {});
  if (key === "indexers") {
    state.indexers = payload?.indexers || [];
    renderIndexers();
  }
  if (key === "applications") {
    state.applications = payload?.applications || [];
    renderApplications();
  }
  if (key === "health") {
    state.alerts = payload?.alerts || [];
    renderHealth();
  }
  if (key === "history") {
    state.events = payload?.events || [];
    renderHealth();
  }
  if (payload?.refreshedAt) noteFreshness(key, payload.refreshedAt);
  return true;
}

async function performLoadAll(force = false, background = false, options = {}) {
  if (!background) {
    clearError();
    setSidebarStatus("Actualisation en cours", "Synchronisation Prowlarr.", "checking");
  }
  const refreshQuery = force ? "?force=true" : "";
  const keys = sectionKeysFor(options);
  const revisionAtStart = state.actionRevision;
  const requests = keys.map((key) => {
    const request = api(route(`${SECTION_ENDPOINTS[key]}${refreshQuery}`), { cache: "no-store" }).then(
      (payload) => {
        if (revisionAtStart === state.actionRevision) applySectionData(key, payload);
        return key;
      },
      (error) => {
        renderSectionError(key, error);
        if (!state.sectionLoaded[key] && key === "overview") showError(error);
        throw error;
      },
    );
    state.sectionPromises[key] = request;
    request.finally(() => {
      if (state.sectionPromises[key] === request) delete state.sectionPromises[key];
    }).catch(() => null);
    return request;
  });
  const results = await Promise.allSettled(requests);
  const failures = results.filter((result) => result.status === "rejected").length;
  if (failures === 0) {
    const timestamps = keys.map((key) => state.sectionUpdatedAt[key]).filter(Boolean).sort();
    if (timestamps.length) setRefreshStamp(timestamps.at(-1));
  } else if (failures < keys.length) {
    setSidebarStatus("Actualisation partielle", `${failures} section(s) en échec.`, "degraded");
    setPageStatus("Certaines sections Prowlarr n'ont pas pu être rafraîchies.", "warn");
  } else {
    setSidebarStatus("Erreur", "Aucune section n'a pu être chargée.", "unavailable");
    setPageStatus("Erreur de chargement Prowlarr.", "error");
  }
}

function beginAction() {
  state.actionRevision += 1;
  state.actionInFlight += 1;
}

function finishAction() {
  state.actionInFlight = Math.max(0, state.actionInFlight - 1);
}

function loadAll(force = false, options = {}) {
  const activeRequests = [state.loadPromise, state.searchPromise].filter(Boolean);
  if (activeRequests.length) {
    if (!force) return state.loadPromise || state.searchPromise;
    if (!state.queuedRefreshPromise) {
      const activeLoad = state.loadPromise;
      const activeSearch = state.searchPromise;
      const queuedRequest = Promise.allSettled(activeRequests).then(() => {
        if (state.loadPromise === activeLoad) state.loadPromise = null;
        if (state.searchPromise === activeSearch) state.searchPromise = null;
        if (state.queuedRefreshPromise === queuedRequest) state.queuedRefreshPromise = null;
        return loadAll(true, options);
      });
      state.queuedRefreshPromise = queuedRequest;
    }
    return state.queuedRefreshPromise;
  }
  const background = Boolean(options.background);
  if (!background) setRefreshBusy(true);
  const request = performLoadAll(force, background, options);
  state.loadPromise = request;
  request.then(
    () => {
      if (state.loadPromise === request) state.loadPromise = null;
      if (!background) setRefreshBusy(false);
    },
    () => {
      if (state.loadPromise === request) state.loadPromise = null;
      if (!background) setRefreshBusy(false);
    },
  );
  return request;
}

function confirmAction(title, message) {
  return new Promise((resolve) => {
    els.confirmTitle.textContent = title;
    els.confirmText.textContent = message;
    const cleanup = (value) => {
      els.cancelConfirm.onclick = null;
      els.acceptConfirm.onclick = null;
      els.confirmDialog.close();
      resolve(value);
    };
    els.cancelConfirm.onclick = () => cleanup(false);
    els.acceptConfirm.onclick = () => cleanup(true);
    els.confirmDialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      cleanup(false);
    }, { once: true });
    openDialog(els.confirmDialog, document.activeElement);
    els.cancelConfirm.focus();
  });
}

async function testIndexer(id) {
  const key = `test:${id}`;
  if (state.busy.has(key)) return;
  setBusy(key, true, "Test en cours...");
  beginAction();
  try {
    await api(route("/api/indexers/test"), { method: "POST", body: JSON.stringify({ id: Number(id) }) });
    showToast("Test d'indexer demande.");
    await loadAll(true);
  } catch (error) {
    showError(error);
  } finally {
    finishAction();
    setBusy(key, false);
  }
}

async function toggleIndexer(buttonNode) {
  const id = Number(buttonNode.dataset.id);
  const enabled = buttonNode.dataset.enabled === "true";
  if (enabled) {
    const ok = await confirmAction("Desactiver l'indexer", `Confirmer la desactivation de ${buttonNode.dataset.name} ?`);
    if (!ok) return;
  }
  const key = `toggle:${id}`;
  if (state.busy.has(key)) return;
  setBusy(key, true, enabled ? "Desactivation..." : "Activation...");
  beginAction();
  try {
    await api(route("/api/indexers/enabled"), { method: "POST", body: JSON.stringify({ id, enabled: !enabled }) });
    showToast(enabled ? "Indexer desactive." : "Indexer active.");
    await loadAll(true);
  } catch (error) {
    showError(error);
  } finally {
    finishAction();
    setBusy(key, false);
  }
}

function searchFingerprint(parameters) {
  return JSON.stringify([
    parameters.query.trim(),
    [...parameters.categories].sort((a, b) => a - b),
    [...parameters.indexerIds].sort((a, b) => a - b),
  ]);
}

function collectSearchParameters() {
  const validIndexerIds = new Set(state.indexers.map((item) => String(item.id)));
  return {
    query: els.releaseQuery.value,
    categories: selectedCategoryIds(),
    indexerIds: [...state.releaseIndexerSelection]
      .filter((id) => validIndexerIds.has(String(id)))
      .map(Number)
      .filter(Number.isFinite),
  };
}

async function runSearch(event) {
  event.preventDefault();
  state.searchInFlight = true;
  if (state.searchAbort) state.searchAbort.abort();
  const requestId = ++state.searchRequestId;
  const controller = new AbortController();
  state.searchAbort = controller;
  let settleSearch;
  const searchPromise = new Promise((resolve) => {
    settleSearch = resolve;
  });
  state.searchPromise = searchPromise;
  setBusy("search", true, "Recherche...");
  try {
    if (state.sectionPromises.indexers) await state.sectionPromises.indexers.catch(() => null);
    if (requestId !== state.searchRequestId) return;
    const parameters = collectSearchParameters();
    const requestFingerprint = searchFingerprint(parameters);
    const payload = await api(route("/api/search"), {
      method: "POST",
      body: JSON.stringify(parameters),
      signal: controller.signal,
    });
    if (requestId !== state.searchRequestId || searchFingerprint(collectSearchParameters()) !== requestFingerprint) return;
    state.results = payload.results || [];
    state.partialFailures = payload.partialFailures || [];
    state.resultPage = 0;
    renderResults();
  } catch (error) {
    if (requestId === state.searchRequestId && error.name !== "AbortError") showError(error);
  } finally {
    if (requestId === state.searchRequestId) {
      state.searchInFlight = false;
      state.searchAbort = null;
      setBusy("search", false);
      updateUrl();
    }
    settleSearch();
    if (state.searchPromise === searchPromise) state.searchPromise = null;
  }
}

function syncReleaseIndexerSelection() {
  [...els.releaseIndexers.options].forEach((option) => {
    if (option.selected) state.releaseIndexerSelection.add(option.value);
    else state.releaseIndexerSelection.delete(option.value);
  });
}

async function grabRelease(buttonNode) {
  const key = buttonNode.dataset.busyKey;
  if (state.busy.has(key)) return;
  setBusy(key, true, "Envoi en cours...");
  beginAction();
  try {
    await api(route("/api/grab"), {
      method: "POST",
      body: JSON.stringify({
        releaseId: buttonNode.dataset.releaseId,
        title: buttonNode.dataset.title,
      }),
    });
    state.lastGrab = {
      title: buttonNode.dataset.title,
      followUrl: buildTorrentFollowUrl(buttonNode.dataset.title, buttonNode.dataset.indexer),
    };
    renderGrabNotice();
    showToast(`Envoye vers qBittorrent : ${buttonNode.dataset.title}`);
  } catch (error) {
    showError(error);
  } finally {
    finishAction();
    setBusy(key, false);
  }
}

function configureLinks() {
  window.DashboardNavigation?.configure(state, "prowlarr");
}

function handleTabKeydown(event) {
  const currentIndex = els.tabs.findIndex((tab) => tab === event.currentTarget);
  if (currentIndex < 0) return;
  const keys = {
    ArrowRight: (currentIndex + 1) % els.tabs.length,
    ArrowLeft: (currentIndex - 1 + els.tabs.length) % els.tabs.length,
    Home: 0,
    End: els.tabs.length - 1,
  };
  if (!(event.key in keys)) return;
  event.preventDefault();
  const next = els.tabs[keys[event.key]];
  setView(next.dataset.view, { focus: true });
}

function bindSectionRetries() {
  document.querySelectorAll("[data-section-retry]").forEach((node) => {
    node.addEventListener("click", async () => {
      await loadAll(true);
    });
  });
}

function debounce(callback, delay) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

const scheduleIndexerRender = debounce(renderIndexers, 70);
const scheduleReleaseIndexerRender = debounce(renderReleaseIndexers, 90);
const scheduleResultRender = debounce(() => {
  state.resultPage = 0;
  renderResults();
}, 90);

function isPanelVisible() {
  return document.visibilityState !== "hidden" && document.hidden !== true;
}

function liveTopicsForView() {
  return sectionKeysFor({}, state.activeView).join(",");
}

function applyLiveFrame(frame) {
  const topic = frame?.topic;
  if (!Object.hasOwn(SECTION_ENDPOINTS, topic)) return;
  const data = { ...(frame.data || {}) };
  const error = data.error;
  delete data.error;
  if (frame.stale || state.actionInFlight > 0) {
    if (error && state.actionInFlight === 0) renderSectionError(topic, error);
    return;
  }
  if (Object.keys(data).length) applySectionData(topic, data);
  if (error) renderSectionError(topic, error);
}

function applyLiveFreshness(frame) {
  const topic = frame?.topic;
  if (!Object.hasOwn(SECTION_ENDPOINTS, topic)) return;
  // collectedAt is the panel's cache-check time, not Prowlarr's source refresh
  // time. Only advance the indicator when the backend supplies an actual
  // source timestamp; unchanged cached data must not be presented as fresh.
  const sourceUpdatedAt = frame.sourceUpdatedAt;
  if (sourceUpdatedAt) noteFreshness(topic, sourceUpdatedAt);
}

async function pollLiveSections({ signal } = {}) {
  const keys = sectionKeysFor({}, state.activeView);
  const settled = await Promise.allSettled(keys.map(async (key) => {
    const payload = await api(route(SECTION_ENDPOINTS[key]), { cache: "no-store", signal });
    return [key, payload];
  }));
  const sections = {};
  settled.forEach((result) => {
    if (result.status === "fulfilled") sections[result.value[0]] = result.value[1];
  });
  return { sections };
}

function disconnectLive() {
  state.live.controller?.stop?.();
  state.live.controller = null;
  state.live.topics = "";
  state.live.connected = false;
}

function connectLive() {
  if (!isPanelVisible()) {
    disconnectLive();
    return;
  }
  if (!window.DashboardLive?.createLiveSubscription) {
    startPolling();
    return;
  }
  const topics = liveTopicsForView();
  if (state.live.controller?.isStarted?.() && state.live.topics === topics) return;
  disconnectLive();
  state.live.topics = topics;
  const controller = window.DashboardLive.createLiveSubscription({
    streamUrl: route(`/api/events?topics=${encodeURIComponent(topics)}`),
    pollIntervalMs: POLL_INTERVAL_MS,
    reconnectDelayMs: 1000,
    heartbeatTimeoutMs: 35000,
    poll: pollLiveSections,
    handlers: {
      snapshot: applyLiveFrame,
      section: applyLiveFrame,
      freshness: applyLiveFreshness,
      status: ({ connected }) => {
        state.live.connected = Boolean(connected);
        if (connected) stopPolling();
      },
      error: (error) => showError(error),
    },
  });
  state.live.controller = controller;
  controller.start();
}

function handleVisibilityChange() {
  if (!isPanelVisible()) {
    disconnectLive();
    stopPolling();
    return;
  }
  connectLive();
}

function stopPolling() {
  if (state.pollTimer === null) return;
  window.clearInterval(state.pollTimer);
  state.pollTimer = null;
}

function startPolling() {
  if (state.pollTimer !== null || !isPanelVisible()) return;
  state.pollTimer = window.setInterval(() => {
    if (!isPanelVisible() || state.live.connected || state.searchInFlight || state.loadPromise) return;
    if (document.activeElement?.matches("input, select, textarea, [contenteditable='true']")) return;
    loadAll(false, { background: true }).catch(showError);
  }, POLL_INTERVAL_MS);
}

function clearReleaseIndexerSelection() {
  state.releaseIndexerSelection.clear();
  [...els.releaseIndexers.options].forEach((option) => {
    option.selected = false;
  });
}

function bindEvents() {
  document.addEventListener("click", (event) => {
    const buttonNode = event.target.closest("button");
    if (!buttonNode) return;
    if (buttonNode.dataset.view) setView(buttonNode.dataset.view, { focus: true });
    if (buttonNode.dataset.action === "test-indexer") testIndexer(buttonNode.dataset.id);
    if (buttonNode.dataset.action === "toggle-indexer") toggleIndexer(buttonNode);
    if (buttonNode.dataset.action === "grab") grabRelease(buttonNode);
    if (buttonNode.dataset.action === "dismiss-grab-notice") {
      state.lastGrab = null;
      renderGrabNotice();
    }
  });
  els.tabs.forEach((tab) => tab.addEventListener("keydown", handleTabKeydown));
  els.refreshButton.addEventListener("click", () => loadAll(true));
  els.retryButton.addEventListener("click", () => loadAll(true));
  els.testAllButton.addEventListener("click", async () => {
    const ok = await confirmAction("Tester tous les indexers", "Cette action interroge tous les indexers configures.");
    if (!ok) return;
    if (state.busy.has("test-all")) return;
    setBusy("test-all", true, "Tests en cours...");
    beginAction();
    try {
      await api(route("/api/indexers/test-all"), { method: "POST", body: "{}" });
      showToast("Test global demande.");
      await loadAll(true);
    } catch (error) {
      showError(error);
    } finally {
      finishAction();
      setBusy("test-all", false);
    }
  });
  els.searchForm.addEventListener("submit", runSearch);
  els.indexerSearch.addEventListener("input", scheduleIndexerRender);
  [els.indexerState, els.indexerProtocol, els.indexerTag, els.indexerSort].forEach((node) => node.addEventListener("input", renderIndexers));
  els.resetIndexers.addEventListener("click", () => {
    els.indexerSearch.value = "";
    els.indexerState.value = "all";
    els.indexerProtocol.value = "all";
    els.indexerTag.value = "all";
    els.indexerSort.value = "name";
    renderIndexers();
  });
  els.resultSearch.addEventListener("input", scheduleResultRender);
  els.resultCategory.addEventListener("input", scheduleResultRender);
  els.resultMaxSize.addEventListener("input", scheduleResultRender);
  [els.resultIndexer, els.resultProtocol, els.resultSort].forEach((node) => node.addEventListener("input", scheduleResultRender));
  els.resultPrevious?.addEventListener("click", () => showResultPage(-1));
  els.resultNext?.addEventListener("click", () => showResultPage(1));
  els.releaseIndexerSearch.addEventListener("input", scheduleReleaseIndexerRender);
  els.releaseIndexers.addEventListener("change", syncReleaseIndexerSelection);
  els.selectAllIndexers.addEventListener("click", () => {
    [...els.releaseIndexers.options].forEach((option) => {
      option.selected = true;
      state.releaseIndexerSelection.add(option.value);
    });
  });
  els.clearIndexers.addEventListener("click", () => {
    clearReleaseIndexerSelection();
  });
  window.addEventListener?.("popstate", () => {
    applyUrlState();
    setView(state.activeView, { replace: false });
  });
  document.addEventListener("visibilitychange", handleVisibilityChange);
  bindSectionRetries();
}

async function init() {
  configureLinks();
  renderGrabNotice();
  renderReleaseCategories();
  bindEvents();
  applyUrlState();
  setView(state.activeView, { replace: false });
  await refreshSession();
  state.initialized = true;
  connectLive();
  await loadAll(false, { sections: VIEW_SECTIONS[state.activeView] });
  state.freshnessTimer = window.setInterval(refreshFreshnessIndicator, 5000);
  const confirmActionName = currentUrl().searchParams.get("confirm");
  if (confirmActionName === "test-all") {
    state.activeView = "indexers";
    setView("indexers", { replace: false });
    showToast("Confirmation requise avant le test global.");
    els.testAllButton.focus();
  }
}

init().catch(showError);
