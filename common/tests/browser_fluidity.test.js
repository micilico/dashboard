// Real-browser regressions using only Chrome and Node's standard library.
// All API data is synthetic; this never contacts the configured services.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "../..");
const chrome = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const output = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-fluidity-"));
const widths = process.env.FLUIDITY_WIDTHS
  ? process.env.FLUIDITY_WIDTHS.split(",").map(Number).filter(Number.isFinite)
  : (process.env.FLUIDITY_SCREENSHOTS ? [1600, 1440, 1024, 768, 375] : [1440]);
const panels = (process.env.FLUIDITY_PANELS || "torrent,cloud,prowlarr").split(",").filter(Boolean);
const useIframe = Boolean(process.env.FLUIDITY_SCREENSHOTS || process.env.FLUIDITY_IFRAME);

function fixtureSetup(panel) {
  const prefix = `/${panel}-panel`;
  window[`__${panel.toUpperCase()}_PANEL_CONFIG__`] = { publicPrefix: prefix };
  const mock = window.__mock = { calls: [], hold: false, abortDelayMs: 0, revision: 0, errors: [] };
  window.addEventListener("error", (e) => mock.errors.push(e.message));
  window.addEventListener("unhandledrejection", (e) => mock.errors.push(String(e.reason)));
  mock.torrents = Array.from({ length: 8 }, (_, i) => ({
    hash: `torrent-${i}`, name: `Archive ${i}`, state: "downloading", progress: 0.5,
    downloadSpeed: 1024, uploadSpeed: 512, uploaded: 2048, ratio: 1, size: 4096,
    eta: 60, addedOn: 1700000000 + i, category: "Archives", tags: "test",
  }));
  mock.files = Array.from({ length: 8 }, (_, i) => ({
    name: `Archive ${i}.txt`, path: `Archive ${i}.txt`, size_bytes: 1024,
    size: "1 Ko", modified: 1700000000, is_dir: false,
  }));
  mock.indexers = Array.from({ length: 8 }, (_, i) => ({
    id: i + 1, name: `Indexer ${i}`, protocol: "torrent", enabled: true,
    health: "ok", priority: i, tags: [], categories: [], lastTest: "OK",
  }));
  mock.results = Array.from({ length: 400 }, (_, i) => ({
    id: `release-${i}`, title: `Archive ${i}`, indexer: "Indexer 0", indexerId: 1,
    category: "Archives", protocol: "torrent", size: 1024, seeders: 10, leechers: 1,
  }));
  mock.payload = (url, options = {}) => {
    const route = new URL(url, "http://fixture.invalid");
    const endpoint = route.pathname.split("/api")[1] || "";
    if (endpoint === "/session") return { csrfToken: "synthetic-test-token" };
    if (endpoint === "/torrents") return { torrents: mock.torrents.map((t, i) => ({ ...t, downloadSpeed: t.downloadSpeed + (i === 0 ? mock.revision : 0) })) };
    if (endpoint === "/dashboard") return {
      overview: { downloadSpeedBytes: 1024, uploadSpeedBytes: 512, activeTorrents: 8 },
      services: ["qBittorrent", "Prowlarr", "rclone", "Jellyfin"].map((name) => ({ name, status: "operational", checkedAt: new Date().toISOString() })),
      alerts: [], recentActivity: [], criticalCount: 0,
    };
    if (endpoint === "/storage") return { disk: {}, rclone: {} };
    if (endpoint === "/activity") return { summary: {}, timeline: [] };
    if (endpoint === "/torrents/stats/trackers") return { stats: { totals: [], daily: [] } };
    if (endpoint === "/overview") return { connection: "ready", indexersActive: 8, indexersError: 0 };
    if (endpoint === "/indexers") return { indexers: mock.indexers };
    if (endpoint === "/applications") return { applications: [] };
    if (endpoint === "/health") return { alerts: [] };
    if (endpoint === "/history") return { events: [] };
    if (endpoint === "/search") {
      let query = "";
      try { query = String(JSON.parse(options.body || "{}").query || "").trim(); } catch {}
      const results = query && query !== "Archive"
        ? mock.results.filter((item) => item.title === query)
        : mock.results;
      return { results, partialFailures: [] };
    }
    if (endpoint === "/favorites") return { items: [], favorites: [] };
    if (endpoint === "/files" || endpoint === "/files/search") {
      const query = route.searchParams.get("search") || route.searchParams.get("q") || "";
      const requestedPath = route.searchParams.get("path") || "";
      const sourceFiles = mock.filesByPath?.[requestedPath] || mock.files;
      const items = sourceFiles.filter((f) => f.name.toLowerCase().includes(query.toLowerCase()));
      const offset = Math.max(0, Number(route.searchParams.get("offset")) || 0);
      const limit = Math.max(1, Number(route.searchParams.get("limit")) || 50);
      return {
        items: items.slice(offset, offset + limit),
        total: items.length,
        has_more: offset + limit < items.length,
        disk_available: false,
      };
    }
    return {};
  };
  window.fetch = async (url, options = {}) => {
    const call = { url: String(url), method: options.method || "GET", startedAt: performance.now(), aborted: false };
    mock.calls.push(call);
    const abort = () => {
      call.aborted = true;
      throw new DOMException("Aborted", "AbortError");
    };
    if (options.signal?.aborted) abort();
    while (mock.hold) {
      await new Promise((resolve) => setTimeout(resolve, mock.abortDelayMs || 10));
      if (options.signal?.aborted) abort();
    }
    return new Response(JSON.stringify(mock.payload(String(url), options)), { headers: { "Content-Type": "application/json" } });
  };
}

async function extendedChecks(panel, { container, refresh, mock, until, check }) {
  if (panel === "torrent" || panel === "cloud") {
    const checkbox = container.querySelector('input[type="checkbox"]');
    checkbox.click();
    check(checkbox.checked, `${panel}: selection updates on the focused row`);
    const selectedRow = container.children[0];
    await refresh();
    check(container.children[0] === selectedRow, `${panel}: refresh preserves selected row identity`);
    check(container.children[0].querySelector('input[type="checkbox"]').checked, `${panel}: refresh preserves selection`);
  }

  const scroller = container.closest(".torrent-table-shell, .table-wrap, .table-scroll") || container.parentElement;
  scroller.style.maxHeight = "120px";
  scroller.style.overflowY = "auto";
  scroller.scrollTop = 60;
  const expectedScroll = scroller.scrollTop;
  await refresh();
  check(Math.abs(scroller.scrollTop - expectedScroll) <= 1, `${panel}: unchanged refresh preserves scroll`);

  if (panel === "torrent") {
    mock.revision += 5000;
    const valueBefore = container.children[0].querySelector('[data-cell="download-speed"]').textContent;
    const refreshedRow = container.children[0];
    await refresh();
    check(container.children[0] === refreshedRow, "torrent: value refresh patches the existing row");
    check(container.children[0].querySelector('[data-cell="download-speed"]').textContent !== valueBefore, "torrent: refreshed values update in place");
  } else if (panel === "cloud") {
    const refreshedRow = container.children[0];
    mock.files[0].size_bytes = 2048;
    mock.files[0].size = "2 Ko";
    await refresh();
    check(container.children[0] === refreshedRow, "cloud: data refresh patches the existing row");
    check(container.children[0].querySelector(".size-cell").textContent.includes("2"), "cloud: refreshed size renders");
  } else {
    const refreshedRow = container.children[0];
    mock.indexers[0].lastTest = "OK après refresh";
    await refresh();
    check(container.children[0] === refreshedRow, "prowlarr: data refresh patches the existing row");
    check(container.children[0].children[6].textContent === "OK après refresh", "prowlarr: refreshed metadata renders");
  }

  mock.hold = true;
  const actionBefore = mock.calls.length;
  if (panel === "torrent") {
    const pause = [...container.children[0].querySelectorAll("button")].find((node) => node.textContent === "Pause");
    pause.click();
    await until(() => mock.calls.slice(actionBefore).some((call) => call.method === "POST" && call.url.endsWith("/api/torrents/pause")), "torrent action request");
    check(container.children[0].classList.contains("is-busy"), "torrent: action marks the target row busy");
  } else if (panel === "cloud") {
    container.children[0].querySelector(".fav-btn").click();
    await until(() => mock.calls.slice(actionBefore).some((call) => call.method === "POST" && call.url.includes("/api/favorites/add")), "cloud action request");
    check(true, "cloud: favorite action targets the selected file");
  } else {
    container.children[0].querySelector('[data-action="test-indexer"]').click();
    await until(() => mock.calls.slice(actionBefore).some((call) => call.method === "POST" && call.url.includes("/api/indexers/test")), "prowlarr action request");
    check(container.children[0].querySelector('[data-action="test-indexer"]').getAttribute("aria-busy") === "true", "prowlarr: indexer test exposes targeted busy state");
  }
  mock.hold = false;
  if (panel === "torrent") await until(() => !state.refreshPromise && !state.forceRefreshQueued && state.globalActionCount === 0, "torrent action completion");
  else if (panel === "cloud") await until(() => !S.activeLoad && !S.queuedLoad, "cloud action completion");
  else await until(() => !state.loadPromise && !state.busy.size, "prowlarr action completion");

  if (panel === "cloud") {
    const originalFiles = mock.files.slice();
    const invoker = container.children[0].querySelector(".action-btn");
    openFileActionMenu(S.files[0], invoker);
    const menu = document.getElementById("fileActionMenu");
    const firstItem = menu.querySelector('[role="menuitem"]');
    firstItem.focus();
    await loadFiles({ reason: "poll" });
    check(!menu.hidden && menu.contains(document.activeElement), "cloud: polling preserves the open menu and its focus");
    container.children[0].remove();
    await loadFiles({ reason: "poll" });
    check(menu.hidden && S.actionMenuTarget === null, "cloud: polling closes a menu whose invoker detached");

    const emptyInvoker = container.children[0].querySelector(".action-btn");
    openFileActionMenu(S.files[0], emptyInvoker);
    mock.files = [];
    await loadFiles({ reason: "poll" });
    check(menu.hidden && S.actionMenuTarget === null, "cloud: polling closes the menu when the empty list removes its target");
    check(!document.getElementById("emptyState").hidden, "cloud: empty polling renders the empty state");
    mock.files = originalFiles;
    await loadFiles({ reason: "manual" });
  }

  if (panel === "cloud") {
    const blockedConflict = {
      code: "destination_exists", kind: "game", source: "Games/Blocked", target: "jeux/Blocked",
      message: "La destination existe déjà.", recovery: "Déplacer l’élément existant.",
    };
    renderOrganizePreview({
      canApply: false,
      games: [{ name: "Blocked", is_dir: true, target: "jeux/Blocked", status: "conflict", conflict: blockedConflict }],
      books: [], conflicts: [blockedConflict],
    });
    const organizeDialog = document.getElementById("organizeDialog");
    const organizeSections = document.getElementById("organizeSections");
    check(document.getElementById("confirmOrganizeBtn").disabled, "cloud: conflicts without ready items disable organize confirmation");
    check(organizeSections.querySelectorAll(".organize-conflict").length === 1, "cloud: a blocked game is displayed only in the conflict section");
    check(organizeSections.textContent.includes("Conflits à résoudre") && !organizeSections.textContent.includes("Jeux"), "cloud: blocked games do not get a duplicate ready section");
    check(qs("#organizeMessage", organizeDialog).textContent.includes("Aucun élément ne peut être rangé"), "cloud: blocked preview explains why confirmation is disabled");

    renderOrganizePreview({
      canApply: true,
      games: [
        { name: "ReadyGame", is_dir: true, target: "jeux/ReadyGame", status: "ready", conflict: null },
        { name: "Blocked", is_dir: true, target: "jeux/Blocked", status: "conflict", conflict: blockedConflict },
      ],
      books: [], conflicts: [blockedConflict],
    });
    check(!document.getElementById("confirmOrganizeBtn").disabled, "cloud: ready games keep confirmation enabled beside a conflict");
    check(organizeSections.querySelectorAll(".organize-conflict").length === 1, "cloud: mixed preview still renders one conflict entry");
    const blockedEntries = [...organizeSections.querySelectorAll(".organize-section-items > li")]
      .filter((node) => node.textContent.includes("Blocked"));
    check(organizeSections.textContent.includes("ReadyGame") && blockedEntries.length === 1, "cloud: mixed preview separates ready media from conflicts");
  }

  if (panel === "prowlarr") {
    setView("search", { push: false });
    const releaseSelect = document.getElementById("releaseIndexers");
    renderReleaseIndexers();
    const stableOption = releaseSelect.options[0];
    renderReleaseIndexers();
    check(releaseSelect.options[0] === stableOption, "prowlarr: unchanged options keep their DOM identity");
    releaseSelect.focus();
    renderReleaseIndexers();
    check(document.activeElement === releaseSelect, "prowlarr: unchanged options preserve focus");
    state.releaseIndexerSelection.add("1");
    state.releaseIndexerSelection.add("2");
    document.getElementById("releaseIndexerSearch").value = "Indexer 0";
    renderReleaseIndexers();
    document.getElementById("clearIndexers").click();
    check(state.releaseIndexerSelection.size === 0, "prowlarr: clear removes hidden indexer selections");
    document.getElementById("releaseIndexerSearch").value = "";
    renderReleaseIndexers();
  }

  await new Promise((resolve) => setTimeout(resolve, 60));
  const hiddenBefore = mock.calls.length;
  Object.defineProperty(document, "hidden", { configurable: true, value: true });
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
  if (panel === "torrent") {
    const hiddenResult = await loadTorrents({ silent: true });
    check(hiddenResult === null, "torrent: background polling returns without loading while hidden");
    check(mock.calls.length === hiddenBefore, "torrent: background polling is suspended while hidden");
  } else if (panel === "cloud") {
    startAutoRefresh();
    check(S.autoRefreshTimer === null && mock.calls.length === hiddenBefore, "cloud: polling is not started while hidden");
  } else {
    document.dispatchEvent(new Event("visibilitychange"));
    check(state.pollTimer === null && mock.calls.length === hiddenBefore, "prowlarr: polling is not started while hidden");
  }
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });

  let queryInput;
  let form;
  if (panel === "cloud") {
    mock.filesByPath = {
      "Folder A": [{ name: "A-only.txt", path: "Folder A/A-only.txt", size_bytes: 32, size: "32 o", modified: 1700000000, is_dir: false }],
      "Folder B": [{ name: "B-only.txt", path: "Folder B/B-only.txt", size_bytes: 64, size: "64 o", modified: 1700000000, is_dir: false }],
    };
    mock.abortDelayMs = 25;
    mock.hold = true;
    const abortStart = mock.calls.length;
    navigate("Folder A");
    await until(() => mock.calls.slice(abortStart).some((call) => call.url.includes("path=Folder+A")), "cloud first navigation request");
    navigate("Folder B");
    await until(() => mock.calls.slice(abortStart).some((call) => call.url.includes("path=Folder+B")), "cloud queued navigation request");
    const firstACall = mock.calls.slice(abortStart).find((call) => call.url.includes("path=Folder+A"));
    check(firstACall?.aborted, "cloud: superseded navigation is aborted");
    navigate("Folder A");
    await until(() => mock.calls.slice(abortStart).filter((call) => call.url.includes("path=Folder+A")).length === 2, "cloud returned navigation request");
    const secondNavigationCalls = mock.calls.slice(abortStart);
    const bCall = secondNavigationCalls.find((call) => call.url.includes("path=Folder+B"));
    check(bCall?.aborted, "cloud: the superseded queued navigation is aborted");
    mock.hold = false;
    await until(() => !S.activeLoad && !S.queuedLoad && S.path === "Folder A" && S.files[0]?.name === "A-only.txt", "cloud A-B-A navigation completion");
    check(S.files.length === 1 && S.files[0].name === "A-only.txt", "cloud: A-B-A keeps the latest path and its matching data");
    mock.filesByPath = {};
    mock.abortDelayMs = 0;
    check(mock.errors.length === 0, "cloud: cancellation completes without browser errors");
  } else if (panel === "prowlarr") {
    setView("search", { push: false });
    queryInput = document.getElementById("releaseQuery");
    form = document.getElementById("searchForm");
    mock.hold = true;
    const searchStart = mock.calls.length;
    queryInput.value = "Archive 1";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await until(() => mock.calls.slice(searchStart).some((call) => call.method === "POST" && call.url.includes("/api/search")), "prowlarr first search request");
    queryInput.value = "Archive 2";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await until(() => mock.calls.slice(searchStart).filter((call) => call.method === "POST" && call.url.includes("/api/search")).length === 2, "prowlarr second search request");
    const searchCalls = mock.calls.slice(searchStart).filter((call) => call.method === "POST" && call.url.includes("/api/search"));
    await until(() => searchCalls[0].aborted, "prowlarr superseded search abort");
    check(searchCalls[0].aborted, "prowlarr: superseded search is aborted");
    mock.hold = false;
    await until(() => !state.searchInFlight && !state.searchPromise, "prowlarr search completion");
    check(state.results.length === 1 && state.results[0].title === "Archive 2", "prowlarr: only the current search result is applied");
  }

  if (panel === "torrent") {
    Object.assign(state.prefs, {
      search: "", status: "all", category: "all", tag: "all", tracker: "all", sort: "default", direction: "asc",
    });
    state.selected = new Set();
    resetTorrentWindow();
    mock.torrents = Array.from({ length: 650 }, (_, i) => ({
      hash: `stress-${i}`, name: `Stress ${i}`, state: "downloading", progress: 0.5,
      downloadSpeed: 1024, uploadSpeed: 512, uploaded: 2048, ratio: 1, size: 4096,
      eta: 60, addedOn: 1700000000 + i, category: "Archives", tags: "test",
    }));
    await refresh();
    check(container.children.length === 300, "torrent: large collections render at most 300 rows initially");
    const firstHash = container.children[0].dataset.hash;
    els.loadMoreRows.click();
    check(container.children.length === 300, "torrent: pagination keeps the DOM strictly bounded to 300 rows");
    check(container.children[0].dataset.hash !== firstHash, "torrent: pagination exposes the next bounded window");
    check(!els.showPreviousRows.hidden, "torrent: paginated windows can return to previous rows");

    mock.torrents = mock.torrents.slice(0, 100);
    await refresh();
    check(state.renderOffset === 0, "torrent: shrinking from 650 to 100 resets an offset of 300 to zero");
    check(container.children.length === 100, "torrent: shrinking from 650 to 100 renders the complete remaining collection");

    mock.torrents = Array.from({ length: 650 }, (_, i) => ({
      hash: `stress-${i}`, name: `Stress ${i}`, state: "downloading", progress: 0.5,
      downloadSpeed: 1024, uploadSpeed: 512, uploaded: 2048, ratio: 1, size: 4096,
      eta: 60, addedOn: 1700000000 + i, category: "Archives", tags: "test",
    }));
    await refresh();
    els.loadMoreRows.click();
    check(state.renderOffset === 300, "torrent: stress window starts on the second page");
    setSort("name");
    check(state.renderOffset === 0, "torrent: changing sort resets pagination from offset 300");
    els.loadMoreRows.click();
    setStatusFromQuickFilter("all");
    check(state.renderOffset === 0, "torrent: changing the quick filter resets pagination from offset 300");
    els.loadMoreRows.click();
    history.replaceState({}, "", `${location.pathname}?view=torrents&status=downloading`);
    dispatchEvent(new PopStateEvent("popstate"));
    check(state.renderOffset === 0, "torrent: popstate resets pagination from offset 300");

    state.selected = new Set(["stress-500"]);
    resetTorrentWindow();
    render();
    els.selectVisible.click();
    check(state.selected.size === 301, "torrent: select all visible adds 300 of 650 filtered rows");
    check(state.selected.has("stress-500"), "torrent: select all visible preserves an explicit selection outside the window");
    state.selected.delete("stress-0");
    render();
    check(els.selectVisible.indeterminate, "torrent: visible selection reports an indeterminate state for 299 of 300 rows");
    check(els.visibleSelectionSummary.textContent === "299 sur 300 sélectionnés", "torrent: selection summary counts only the rendered window");
    els.selectVisible.checked = false;
    els.selectVisible.dispatchEvent(new Event("change"));
    check(state.selected.size === 1 && state.selected.has("stress-500"), "torrent: deselecting visible rows preserves selection outside the window");
  } else if (panel === "cloud") {
    mock.files = Array.from({ length: 400 }, (_, i) => ({
      name: `Stress ${i}.txt`, path: `Stress ${i}.txt`, size_bytes: 1024, size: "1 Ko",
      modified: 1700000000 + i, is_dir: false,
    }));
    await refresh();
    check(container.children.length === 50, "cloud: each rendered page is bounded to 50 files");
  } else {
    queryInput.value = "Archive";
    form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    await until(() => !state.searchInFlight && state.results.length === 400, "prowlarr stress search completion");
    check(els.resultRows.children.length === 80, "prowlarr: release results render at most 80 rows per page");
    const resultTable = els.resultRows.closest(".table-scroll").querySelector("table");
    window.scrollTo(0, 500);
    const simpleRefreshScrollY = window.scrollY;
    renderResults();
    check(Math.abs(window.scrollY - simpleRefreshScrollY) <= 1, "prowlarr: simple refresh preserves window scroll");
    window.scrollTo(0, document.documentElement.scrollHeight);
    const beforePageChange = window.scrollY;
    els.resultNext.click();
    check(state.resultPage === 1 && window.scrollY < beforePageChange, "prowlarr: changing result page moves the window back to the results");
    check(Math.abs(resultTable.getBoundingClientRect().top) <= 80, "prowlarr: pagination aligns the results table with the viewport start");
  }
}

async function browserChecks(panel, screenshot) {
  const checks = [];
  function check(value, message) {
    if (!value) throw new Error(message);
    checks.push(message);
  }
  async function until(predicate, message) {
    for (let i = 0; i < 250; i++) {
      if (predicate()) return;
      await new Promise((r) => setTimeout(r, 10));
    }
    throw new Error(message);
  }
  try {
    const mock = window.__mock;
    let container, refresh;
    if (panel === "torrent") {
      await until(() => state.lastUpdatedAt && !state.refreshPromise, "torrent initialization");
      setView(screenshot ? "home" : "torrents", { push: false });
      state.prefs.autoRefresh = false;
      restartRefreshTimer();
      container = els.rows;
      refresh = () => loadTorrents({ force: true });
    } else if (panel === "cloud") {
      await until(() => S.files.length && !S.loading, "cloud initialization");
      stopAutoRefresh();
      container = document.getElementById("fileBody");
      refresh = () => loadFiles(false);
    } else {
      await until(() => state.indexers.length && state.sectionLoaded.indexers, "prowlarr initialization");
      container = els.indexerRows;
      refresh = () => loadAll();
    }
    check(container.children.length === 8, `${panel}: initial list rendered`);
    const row = container.children[0];
    const control = [...row.querySelectorAll("button, a, input, select")]
      .find((node) => node.getClientRects().length > 0 && !node.disabled);
    check(Boolean(control), `${panel}: initial row has a visible keyboard control`);
    if (!screenshot) control.focus();
    await refresh();
    check(container.children[0] === row, `${panel}: unchanged refresh preserves row identity`);
    if (!screenshot) check(document.activeElement === control, `${panel}: unchanged refresh preserves keyboard focus`);
    mock.hold = true;
    const before = mock.calls.length;
    const first = refresh();
    const second = refresh();
    check(container.children[0] === row, `${panel}: data stays visible during refresh`);
    await new Promise((r) => setTimeout(r, 20));
    const calls = mock.calls.slice(before).map((c) => c.url);
    check(new Set(calls).size === calls.length, `${panel}: concurrent refreshes do not duplicate API requests`);
    mock.hold = false;
    await Promise.all([first, second]);
    if (!screenshot) await extendedChecks(panel, { container, refresh, mock, until, check });
    check(mock.errors.length === 0, `${panel}: no browser exceptions`);
    const report = document.createElement("pre");
    report.id = "fluidity-report";
    report.hidden = true;
    report.textContent = JSON.stringify({
      ok: true,
      checks,
      errors: mock.errors,
      viewportWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      overflowElements: [...document.querySelectorAll("body *")]
        .map((node) => ({ node, rect: node.getBoundingClientRect() }))
        .filter(({ rect }) => rect.width > 0 && rect.height > 0 && rect.right > document.documentElement.clientWidth + 1)
        .map(({ node, rect }) => `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ").join(".")}@${Math.round(rect.right)}`)
        .slice(0, 20),
    });
    document.body.append(report);
  } catch (e) {
    window.__mock.hold = false;
    const report = document.createElement("pre");
    report.id = "fluidity-report";
    report.textContent = JSON.stringify({
      ok: false,
      checks,
      error: e.stack,
      errors: window.__mock?.errors,
      viewportWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      overflowElements: [...document.querySelectorAll("body *")]
        .map((node) => ({ node, rect: node.getBoundingClientRect() }))
        .filter(({ node, rect }) => rect.width > 0 && rect.height > 0 && rect.right > document.documentElement.clientWidth + 1)
        .map(({ node, rect }) => `${node.tagName.toLowerCase()}.${String(node.className || "").split(" ").join(".")}@${Math.round(rect.right)}`)
        .slice(0, 20),
    });
    document.body.append(report);
  }
}

for (const panel of panels) {
  const staticDir = path.join(root, `${panel}-panel`, `${panel}_panel`, "static");
  let html = fs.readFileSync(path.join(staticDir, "index.html"), "utf8");
  html = html.replace(/<script\b[^>]*src=["'][^"']+["'][^>]*><\/script>/g, "");
  html = html.replace(/(href|src)="\.\/static\/([^"?]+)(?:\?[^" ]*)?"/g,
    (_, attr, relative) => `${attr}="${pathToFileURL(path.join(staticDir, relative)).href}"`);
  const setup = `(${fixtureSetup.toString()})(${JSON.stringify(panel)});`;
  const script = `<script>${setup}</script><script src="${pathToFileURL(path.join(staticDir, "dist/app.min.js")).href}"></script>`;
  html = html.replace("</body>", `${script}<script>${extendedChecks.toString()}; window.addEventListener("load", () => (${browserChecks.toString()})(${JSON.stringify(panel)}, ${Boolean(process.env.FLUIDITY_SCREENSHOTS)}));</script></body>`);
  const file = path.join(output, `${panel}.html`);
  fs.writeFileSync(file, html);
  for (const width of widths) {
    const wrapper = path.join(output, `${panel}-${width}.html`);
    const copyReport = useIframe && !process.env.FLUIDITY_SCREENSHOTS
      ? `<script>(() => { const frame = document.querySelector("iframe"); const poll = () => { const source = frame.contentDocument?.querySelector("#fluidity-report"); if (source) { const target = document.createElement("pre"); target.id = "fluidity-report"; target.hidden = true; target.textContent = source.textContent; document.body.append(target); return; } setTimeout(poll, 20); }; poll(); })();</script>`
      : "";
    if (useIframe) {
      fs.writeFileSync(wrapper, `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;overflow:hidden;background:#07080b}iframe{display:block;width:${width}px;height:1100px;border:0}</style><iframe src="${pathToFileURL(file).href}"></iframe>${copyReport}`, "utf8");
    }
    if (process.env.FLUIDITY_SCREENSHOTS) {
      const screenshotPath = path.join(output, `${panel}-${width}.png`);
      const args = ["--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
        `--user-data-dir=${path.join(output, `profile-${panel}-${width}`)}`,
        "--hide-scrollbars", "--run-all-compositor-stages-before-draw", "--virtual-time-budget=5000",
        `--window-size=${Math.max(500, width)},1100`, `--screenshot=${screenshotPath}`, pathToFileURL(wrapper).href];
      const result = spawnSync(chrome, args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, timeout: 30000 });
      assert.equal(result.status, 0, result.stderr || String(result.error));
      if (width < 500) {
        const cropped = screenshotPath.replace(/\.png$/, "-cropped.png");
        // A nonzero horizontal offset keeps sips from centering the crop.
        const crop = spawnSync("/usr/bin/sips", ["--cropToHeightWidth", "1100", String(width), "--cropOffset", "0", "1", screenshotPath, "--out", cropped], { encoding: "utf8" });
        assert.equal(crop.status, 0, crop.stderr || String(crop.error));
        fs.renameSync(cropped, screenshotPath);
      }
      console.log(`${panel} ${width}px: ${screenshotPath}`);
      continue;
    }
    const targetFile = useIframe ? wrapper : file;
    const args = ["--headless=new", "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
      `--user-data-dir=${path.join(output, `profile-${panel}-${width}`)}`,
      "--hide-scrollbars", "--virtual-time-budget=6000", `--window-size=${useIframe ? Math.max(500, width) : width},1100`, "--dump-dom", pathToFileURL(targetFile).href];
    const result = spawnSync(chrome, args, { encoding: "utf8", maxBuffer: 16 * 1024 * 1024, timeout: 30000 });
    assert.equal(result.status, 0, result.stderr || String(result.error));
    const match = result.stdout.match(/<pre id="fluidity-report"[^>]*>([\s\S]*?)<\/pre>/);
    assert.ok(match, `${panel} ${width}: browser report missing`);
    const report = JSON.parse(match[1].replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&"));
    assert.equal(report.ok, true, JSON.stringify(report, null, 2));
    assert.equal(report.viewportWidth, width, `${panel} ${width}px: actual browser viewport must match`);
    assert.ok(report.scrollWidth <= report.clientWidth + 1, `${panel} ${width}px: horizontal overflow`);
    console.log(`${panel} ${width}px: ${report.checks.length} browser checks passed`);
  }
}
console.log(`Browser artifacts: ${output}`);
