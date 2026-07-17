const state = {
  csrfToken: "",
  indexers: [],
  results: [],
  applications: [],
  alerts: [],
  events: [],
  capabilities: {},
  busy: new Set(),
  activeView: "indexers",
  searchAbort: null,
};

const els = {
  refreshStatus: document.querySelector("#refreshStatus"),
  refreshButton: document.querySelector("#refreshButton"),
  retryButton: document.querySelector("#retryButton"),
  summaryGrid: document.querySelector("#summaryGrid"),
  alert: document.querySelector("#alert"),
  alertText: document.querySelector("#alertText"),
  tabs: document.querySelectorAll(".tab"),
  views: {
    indexers: document.querySelector("#indexersView"),
    search: document.querySelector("#searchView"),
    applications: document.querySelector("#applicationsView"),
    health: document.querySelector("#healthView"),
  },
  indexersSummary: document.querySelector("#indexersSummary"),
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
  releaseIndexers: document.querySelector("#releaseIndexers"),
  searchButton: document.querySelector("#searchButton"),
  searchSummary: document.querySelector("#searchSummary"),
  resultRows: document.querySelector("#resultRows"),
  resultsEmpty: document.querySelector("#resultsEmpty"),
  resultSearch: document.querySelector("#resultSearch"),
  resultIndexer: document.querySelector("#resultIndexer"),
  resultProtocol: document.querySelector("#resultProtocol"),
  resultCategory: document.querySelector("#resultCategory"),
  resultMaxSize: document.querySelector("#resultMaxSize"),
  resultSort: document.querySelector("#resultSort"),
  appsGrid: document.querySelector("#appsGrid"),
  appsSummary: document.querySelector("#appsSummary"),
  alertsList: document.querySelector("#alertsList"),
  historyList: document.querySelector("#historyList"),
  healthSummary: document.querySelector("#healthSummary"),
  confirmDialog: document.querySelector("#confirmDialog"),
  confirmTitle: document.querySelector("#confirmTitle"),
  confirmText: document.querySelector("#confirmText"),
  cancelConfirm: document.querySelector("#cancelConfirm"),
  acceptConfirm: document.querySelector("#acceptConfirm"),
  toast: document.querySelector("#toast"),
};

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function text(value, fallback = "—") {
  const cleaned = String(value ?? "").trim();
  return cleaned || fallback;
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
  if ((options.method || "GET").toUpperCase() !== "GET") headers.set("X-Prowlarr-Panel-CSRF", state.csrfToken);
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

function setBusy(key, busy) {
  if (busy) state.busy.add(key);
  else state.busy.delete(key);
  document.querySelectorAll(`[data-busy-key="${CSS.escape(key)}"]`).forEach((button) => {
    button.disabled = busy;
    button.textContent = busy ? "En cours..." : button.dataset.label;
  });
}

function fillSelect(select, entries, current = "all") {
  select.innerHTML = entries.map(([value, label]) => `<option value="${value}">${label}</option>`).join("");
  select.value = current;
}

function renderSummary(overview) {
  const stats = [
    ["Connexion", overview.connection === "ready" ? "OK" : "Erreur"],
    ["Version", overview.version],
    ["Indexers", overview.indexersTotal],
    ["Actifs", overview.indexersActive],
    ["Désactivés", overview.indexersDisabled],
    ["En erreur", overview.indexersError],
    ["Apps", overview.applicationsTotal],
    ["Alertes", overview.systemWarnings],
  ];
  els.summaryGrid.innerHTML = stats.map(([label, value]) => `<article class="stat"><strong>${text(value, "0")}</strong><span>${label}</span></article>`).join("");
  state.capabilities = overview.capabilities || {};
  els.refreshStatus.textContent = `Actualisé ${new Date().toLocaleTimeString("fr-FR")}`;
}

function indexerHealthLabel(indexer) {
  if (indexer.health === "error") return "Erreur";
  if (!indexer.enabled) return "Désactivé";
  return "Actif";
}

function renderIndexerFilters() {
  const protocols = [...new Set(state.indexers.map((item) => item.protocol).filter(Boolean))].sort();
  const tags = [...new Set(state.indexers.flatMap((item) => item.tags || []))].sort((a, b) => String(a).localeCompare(String(b)));
  fillSelect(els.indexerState, [["all", "Tous"], ["active", "Actifs"], ["disabled", "Désactivés"], ["error", "En erreur"]], els.indexerState.value || "all");
  fillSelect(els.indexerProtocol, [["all", "Tous"], ...protocols.map((item) => [item, item])], els.indexerProtocol.value || "all");
  fillSelect(els.indexerTag, [["all", "Tous"], ...tags.map((item) => [item, item])], els.indexerTag.value || "all");
  fillSelect(els.indexerSort, [["name", "Nom"], ["priority", "Priorité"], ["state", "État"]], els.indexerSort.value || "name");
}

function filteredIndexers() {
  const query = els.indexerSearch.value.trim().toLowerCase();
  const stateFilter = els.indexerState.value;
  const protocol = els.indexerProtocol.value;
  const tag = els.indexerTag.value;
  const sort = els.indexerSort.value;
  return [...state.indexers]
    .filter((item) => !query || item.name.toLowerCase().includes(query))
    .filter((item) => stateFilter === "all" || (stateFilter === "active" && item.enabled && item.health !== "error") || (stateFilter === "disabled" && !item.enabled) || (stateFilter === "error" && item.health === "error"))
    .filter((item) => protocol === "all" || item.protocol === protocol)
    .filter((item) => tag === "all" || (item.tags || []).map(String).includes(tag))
    .sort((a, b) => {
      if (sort === "priority") return Number(a.priority || 0) - Number(b.priority || 0);
      if (sort === "state") return indexerHealthLabel(a).localeCompare(indexerHealthLabel(b));
      return a.name.localeCompare(b.name);
    });
}

function renderIndexers() {
  renderIndexerFilters();
  const rows = filteredIndexers();
  els.indexersSummary.textContent = `${rows.length} affiché(s) sur ${state.indexers.length}.`;
  els.indexersEmpty.hidden = rows.length > 0;
  els.releaseIndexers.innerHTML = state.indexers.map((item) => `<option value="${item.id}">${item.name}</option>`).join("");
  els.indexerRows.innerHTML = rows.map((item) => {
    const health = item.health === "error" ? "error" : item.enabled ? "ok" : "disabled";
    const toggleLabel = item.enabled ? "Désactiver" : "Activer";
    return `
      <tr>
        <td class="name-cell">${text(item.name)}</td>
        <td>${text(item.protocol)}</td>
        <td><span class="badge ${health}">${indexerHealthLabel(item)}</span>${item.error ? `<div class="muted">${text(item.error)}</div>` : ""}</td>
        <td class="mono">${text(item.priority, "0")}</td>
        <td>${(item.tags || []).map((tag) => `<span class="badge">${tag}</span>`).join(" ") || "—"}</td>
        <td>${Array.isArray(item.categories) ? item.categories.length : "—"}</td>
        <td>${text(item.lastTest)}</td>
        <td>
          <div class="action-row">
            <button class="button secondary" type="button" data-action="test-indexer" data-id="${item.id}" data-busy-key="test:${item.id}" data-label="Tester">Tester</button>
            <button class="button ${item.enabled ? "danger" : "secondary"}" type="button" data-action="toggle-indexer" data-id="${item.id}" data-enabled="${item.enabled}" data-name="${item.name}" data-busy-key="toggle:${item.id}" data-label="${toggleLabel}">${toggleLabel}</button>
          </div>
        </td>
      </tr>`;
  }).join("");
}

function filteredResults() {
  const query = els.resultSearch.value.trim().toLowerCase();
  const indexer = els.resultIndexer.value;
  const protocol = els.resultProtocol.value;
  const category = els.resultCategory.value.trim().toLowerCase();
  const maxSize = Number(els.resultMaxSize.value || 0) * 1024 ** 3;
  const sort = els.resultSort.value || "seeders";
  return [...state.results]
    .filter((item) => !query || item.title.toLowerCase().includes(query))
    .filter((item) => indexer === "all" || item.indexer === indexer)
    .filter((item) => protocol === "all" || item.protocol === protocol)
    .filter((item) => !category || item.category.toLowerCase().includes(category))
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
  fillSelect(els.resultSort, [["seeders", "Seeders"], ["size", "Taille"], ["age", "Âge"], ["indexer", "Indexer"]], els.resultSort.value || "seeders");
}

function renderResults() {
  renderResultFilters();
  const rows = filteredResults();
  els.searchSummary.textContent = `${rows.length} résultat(s) affiché(s). Les URLs privées restent côté serveur.`;
  els.resultsEmpty.hidden = rows.length > 0;
  els.resultRows.innerHTML = rows.map((item) => `
    <tr>
      <td class="name-cell">${text(item.title)}</td>
      <td>${text(item.indexer)}</td>
      <td>${text(item.category)}</td>
      <td class="mono">${formatBytes(item.size)}</td>
      <td>${text(item.age)}</td>
      <td class="mono">${text(item.seeders, "0")}</td>
      <td class="mono">${text(item.leechers, "0")}</td>
      <td>${text(item.protocol)}</td>
      <td>${item.freeleech ? "Freeleech" : `DL ${text(item.downloadFactor)} / UL ${text(item.uploadFactor)}`}</td>
      <td><button class="button primary" type="button" data-action="grab" data-guid="${item.guid}" data-indexer-id="${item.indexerId || ""}" data-title="${item.title}" data-busy-key="grab:${item.id}" data-label="Envoyer">Envoyer</button></td>
    </tr>`).join("");
}

function renderApplications() {
  els.appsSummary.textContent = `${state.applications.length} application(s) configurée(s).`;
  els.appsGrid.innerHTML = state.applications.map((item) => `
    <article class="card">
      <h3>${text(item.name)}</h3>
      <dl>
        <dt>Type</dt><dd>${text(item.type)}</dd>
        <dt>État</dt><dd><span class="badge ${item.enabled ? "ok" : "disabled"}">${item.enabled ? "Active" : "Désactivée"}</span></dd>
        <dt>Test</dt><dd>${text(item.lastTest)}</dd>
        <dt>Sync</dt><dd>${text(item.syncLevel)}</dd>
        <dt>Tags</dt><dd>${(item.tags || []).join(", ") || "—"}</dd>
      </dl>
    </article>`).join("") || `<div class="empty">Aucune application à afficher.</div>`;
}

function renderHealth() {
  els.healthSummary.textContent = `${state.alerts.length} alerte(s), ${state.events.length} événement(s).`;
  els.alertsList.innerHTML = state.alerts.map((item) => `<article class="list-item"><strong>${text(item.source)}</strong><span>${text(item.message)}</span><span class="muted">${text(item.type)} ${text(item.date, "")}</span></article>`).join("") || `<div class="empty">Aucune alerte système.</div>`;
  els.historyList.innerHTML = state.events.map((item) => `<article class="list-item"><strong>${text(item.title, item.eventType)}</strong><span>${text(item.indexer)} ${text(item.result, "")}</span><span class="muted">${text(item.date)}</span></article>`).join("") || `<div class="empty">Aucun événement récent.</div>`;
}

async function loadAll() {
  clearError();
  els.refreshStatus.textContent = "Actualisation...";
  try {
    const [overview, indexers, applications, health, history] = await Promise.all([
      api("api/overview", { cache: "no-store" }),
      api("api/indexers", { cache: "no-store" }),
      api("api/applications", { cache: "no-store" }),
      api("api/health", { cache: "no-store" }),
      api("api/history", { cache: "no-store" }),
    ]);
    state.indexers = indexers.indexers || [];
    state.applications = applications.applications || [];
    state.alerts = health.alerts || [];
    state.events = history.events || [];
    renderSummary(overview);
    renderIndexers();
    renderApplications();
    renderHealth();
  } catch (error) {
    els.refreshStatus.textContent = "Erreur";
    showError(error);
  }
}

function setView(view) {
  state.activeView = view;
  Object.entries(els.views).forEach(([name, element]) => {
    element.hidden = name !== view;
  });
  els.tabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.view === view)));
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
    els.confirmDialog.addEventListener("cancel", () => cleanup(false), { once: true });
    els.confirmDialog.showModal();
  });
}

async function testIndexer(id) {
  const key = `test:${id}`;
  if (state.busy.has(key)) return;
  setBusy(key, true);
  try {
    await api("api/indexers/test", { method: "POST", body: JSON.stringify({ id: Number(id) }) });
    showToast("Test d'indexer demandé.");
    await loadAll();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(key, false);
  }
}

async function toggleIndexer(button) {
  const id = Number(button.dataset.id);
  const enabled = button.dataset.enabled === "true";
  if (enabled) {
    const ok = await confirmAction("Désactiver l'indexer", `Confirmer la désactivation de ${button.dataset.name} ?`);
    if (!ok) return;
  }
  const key = `toggle:${id}`;
  if (state.busy.has(key)) return;
  setBusy(key, true);
  try {
    await api("api/indexers/enabled", { method: "POST", body: JSON.stringify({ id, enabled: !enabled }) });
    showToast(enabled ? "Indexer désactivé." : "Indexer activé.");
    await loadAll();
  } catch (error) {
    showError(error);
  } finally {
    setBusy(key, false);
  }
}

async function runSearch(event) {
  event.preventDefault();
  if (state.searchAbort) state.searchAbort.abort();
  state.searchAbort = new AbortController();
  const categories = els.releaseCategories.value.split(",").map((item) => Number(item.trim())).filter(Number.isFinite);
  const indexerIds = [...els.releaseIndexers.selectedOptions].map((option) => Number(option.value)).filter(Number.isFinite);
  els.searchButton.disabled = true;
  els.searchButton.textContent = "Recherche...";
  try {
    const payload = await api("api/search", {
      method: "POST",
      body: JSON.stringify({ query: els.releaseQuery.value, categories, indexerIds }),
      signal: state.searchAbort.signal,
    });
    state.results = payload.results || [];
    renderResults();
  } catch (error) {
    if (error.name !== "AbortError") showError(error);
  } finally {
    els.searchButton.disabled = false;
    els.searchButton.textContent = "Rechercher";
  }
}

async function grabRelease(button) {
  const key = button.dataset.busyKey;
  if (state.busy.has(key)) return;
  setBusy(key, true);
  try {
    await api("api/grab", {
      method: "POST",
      body: JSON.stringify({
        guid: button.dataset.guid,
        indexerId: button.dataset.indexerId ? Number(button.dataset.indexerId) : null,
        title: button.dataset.title,
      }),
    });
    showToast(`Envoyé vers qBittorrent : ${button.dataset.title}`);
  } catch (error) {
    showError(error);
  } finally {
    setBusy(key, false);
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.view) setView(button.dataset.view);
  if (button.dataset.action === "test-indexer") testIndexer(button.dataset.id);
  if (button.dataset.action === "toggle-indexer") toggleIndexer(button);
  if (button.dataset.action === "grab") grabRelease(button);
});

els.refreshButton.addEventListener("click", loadAll);
els.retryButton.addEventListener("click", loadAll);
els.testAllButton.addEventListener("click", async () => {
  const ok = await confirmAction("Tester tous les indexers", "Cette action interroge tous les indexers configurés.");
  if (!ok) return;
  try {
    await api("api/indexers/test-all", { method: "POST", body: "{}" });
    showToast("Test global demandé.");
    await loadAll();
  } catch (error) {
    showError(error);
  }
});
els.searchForm.addEventListener("submit", runSearch);
[els.indexerSearch, els.indexerState, els.indexerProtocol, els.indexerTag, els.indexerSort].forEach((element) => element.addEventListener("input", renderIndexers));
els.resetIndexers.addEventListener("click", () => {
  els.indexerSearch.value = "";
  els.indexerState.value = "all";
  els.indexerProtocol.value = "all";
  els.indexerTag.value = "all";
  els.indexerSort.value = "name";
  renderIndexers();
});
[els.resultSearch, els.resultIndexer, els.resultProtocol, els.resultCategory, els.resultMaxSize, els.resultSort].forEach((element) => element.addEventListener("input", renderResults));

refreshSession().then(loadAll).catch(showError);
