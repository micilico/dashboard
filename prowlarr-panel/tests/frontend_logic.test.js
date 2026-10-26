const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

let fakeActiveElement = null;

class FakeElement {
  constructor(tag = "div", selector = "") {
    this.tagName = tag.toUpperCase();
    this.selector = selector;
    this.children = [];
    this.childNodes = this.children;
    this.dataset = {};
    this.className = "";
    this.attributes = {};
    this.hidden = false;
    this.value = "";
    this.textContent = "";
    this.checked = false;
    this.selected = false;
    this.disabled = false;
    this.open = false;
    this.parentNode = null;
    this.isConnected = true;
  }

  append(...items) {
    items.forEach((item) => this._appendOne(item));
  }

  appendChild(item) {
    this._appendOne(item);
    return item;
  }

  _appendOne(item) {
    if (item === null || item === undefined) return;
    if (typeof item === "string") item = new FakeText(item);
    item.parentNode = this;
    item.isConnected = true;
    this.children.push(item);
    if (this.options || this.tagName === "SELECT") this.options = this.children.filter((child) => child.tagName === "OPTION");
  }

  replaceChildren(...items) {
    this.children = [];
    this.childNodes = this.children;
    this.append(...items);
    if (this.options || this.tagName === "SELECT") this.options = this.children.filter((child) => child.tagName === "OPTION");
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  insertBefore(item, reference = null) {
    if (item.parentNode) item.parentNode.removeChild(item);
    const index = reference ? this.children.indexOf(reference) : -1;
    if (index < 0) this.children.push(item);
    else this.children.splice(index, 0, item);
    item.parentNode = this;
    item.isConnected = true;
    if (this.options || this.tagName === "SELECT") this.options = this.children.filter((child) => child.tagName === "OPTION");
    return item;
  }

  removeChild(item) {
    const index = this.children.indexOf(item);
    if (index >= 0) this.children.splice(index, 1);
    item.parentNode = null;
    const disconnect = (node) => {
      node.isConnected = false;
      if (fakeActiveElement === node) fakeActiveElement = null;
      (node.children || []).forEach(disconnect);
    };
    disconnect(item);
    if (this.options || this.tagName === "SELECT") this.options = this.children.filter((child) => child.tagName === "OPTION");
    return item;
  }

  remove() {
    this.parentNode?.removeChild(this);
  }

  getAttribute(name) {
    return this.attributes[name];
  }

  closest() {
    return null;
  }

  getBoundingClientRect() {
    return { top: 0 };
  }

  addEventListener() {}
  focus() { fakeActiveElement = this; }
  close() { this.open = false; }
  showModal() { this.open = true; }

  querySelector(selector) {
    if (selector === "span") {
      return this.children.find((child) => child.tagName === "SPAN") || null;
    }
    return null;
  }

  querySelectorAll(selector) {
    if (selector === 'input[type="checkbox"]:checked') {
      const matches = [];
      const walk = (node) => {
        node.children.forEach((child) => {
          if (child.tagName === "INPUT" && child.attributes.type === "checkbox" && child.checked) matches.push(child);
          if (child.children) walk(child);
        });
      };
      walk(this);
      return matches;
    }
    return [];
  }

  get selectedOptions() {
    return (this.options || []).filter((option) => option.selected);
  }
}

class FakeText {
  constructor(text) {
    this.tagName = "#TEXT";
    this.textContent = String(text);
  }
}

function flattenText(node) {
  if (!node) return "";
  if (node.tagName === "#TEXT") return node.textContent;
  return `${node.textContent || ""}${(node.children || []).map(flattenText).join("")}`;
}

function createEnvironment() {
  const elements = new Map();
  const tabs = ["indexers", "search", "applications", "health"].map((view) => {
    const tab = new FakeElement("button", ".tab");
    tab.dataset.view = view;
    return tab;
  });

  const ensure = (selector, tag = "div") => {
    if (!elements.has(selector)) elements.set(selector, new FakeElement(tag, selector));
    return elements.get(selector);
  };

  [
    "#refreshStatus", "#refreshButton", "#retryButton", "#alert", "#alertText", "#summaryGrid",
    "#homeLink", "#activityLink", "#torrentLink", "#prowlarrLink", "#storageLink", "#mediaLink", "#healthLink",
    "#indexersView", "#searchView", "#applicationsView",
    "#healthView", "#indexersSummary", "#indexersError", "#indexerRows", "#indexersEmpty", "#indexerSearch",
    "#indexerState", "#indexerProtocol", "#indexerTag", "#indexerSort", "#resetIndexers", "#testAllButton",
    "#searchForm", "#releaseQuery", "#releaseCategories", "#releaseCategoryChoices", "#releaseIndexerSearch",
    "#releaseIndexers", "#selectAllIndexers", "#clearIndexers", "#searchButton", "#searchSummary", "#resultRows",
    "#searchPartialFailures", "#grabNotice", "#resultsEmpty", "#resultSearch", "#resultIndexer", "#resultProtocol", "#resultCategory", "#resultMaxSize",
    "#resultSort", "#resultPagination", "#resultRange", "#resultPrevious", "#resultNext", "#appsGrid", "#appsSummary", "#appsError", "#alertsList", "#historyList", "#healthSummary",
    "#healthError", "#healthTab", "#confirmDialog", "#confirmTitle", "#confirmText", "#cancelConfirm",
    "#acceptConfirm", "#toast",
  ].forEach((selector) => ensure(selector, selector.includes("select") ? "select" : "div"));

  ["#indexerState", "#indexerProtocol", "#indexerTag", "#indexerSort", "#releaseIndexers", "#resultIndexer", "#resultProtocol", "#resultSort"].forEach((selector) => {
    ensure(selector, "select").options = [];
  });

  ["#indexersError", "#appsError", "#healthError"].forEach((selector) => {
    const container = ensure(selector);
    container.append(new FakeElement("span"), new FakeElement("button"));
  });

  const context = {
    console,
    AbortController,
    FormData,
    URLSearchParams,
    CSS: { escape(value) { return String(value); } },
    Headers: class Headers {
      constructor(init = {}) { this.map = new Map(Object.entries(init)); }
      set(key, value) { this.map.set(key, value); }
      has(key) { return this.map.has(key); }
    },
    fetch: async () => ({ ok: true, json: async () => ({ csrfToken: "token" }) }),
    setTimeout() { return 1; },
    clearTimeout() {},
    window: {
      __PROWLARR_PANEL_CONFIG__: { publicPrefix: "/prowlarr-panel", torrentPanelPrefix: "/torrent-panel" },
      location: { href: "http://localhost/prowlarr-panel/" },
      history: { replaceState() {}, pushState() {} },
      scrollY: 420,
      pageYOffset: 420,
      scrollCalls: [],
      scrollTo(options) { this.scrollCalls.push(options); },
      setTimeout() { return 1; },
      clearTimeout() {},
      setInterval() { return 2; },
      clearInterval() {},
      addEventListener() {},
    },
    document: {
      get activeElement() { return fakeActiveElement; },
      getElementById() { return null; },
      querySelector(selector) { return ensure(selector); },
      querySelectorAll(selector) { return selector === ".tab" ? tabs : []; },
      createElement(tag) { return new FakeElement(tag); },
      createTextNode(text) { return new FakeText(text); },
      addEventListener() {},
    },
    URL,
  };

  return { context, elements };
}

const { context, elements } = createEnvironment();
const domSource = fs.readFileSync("common/js/dom.js", "utf8");
const commonApiSource = fs.readFileSync("common/js/api.js", "utf8");
const source = fs.readFileSync("prowlarr-panel/prowlarr_panel/static/app.js", "utf8").replace(
  "init().catch(showError);",
  "globalThis.__testApi = { state, renderIndexers, renderResults, renderReleaseIndexers, clearReleaseIndexerSelection, renderGrabNotice, buildTorrentFollowUrl, setView, handleTabKeydown, showResultPage, searchFingerprint, loadAll, runSearch, applyLiveFrame, applySectionData };",
);
vm.runInNewContext(domSource, context);
vm.runInNewContext(commonApiSource, context);
vm.runInNewContext(source, context);
const api = context.__testApi;

api.state.indexers = [
  {
    id: 7,
    name: '<img src=x onerror=alert(1)>',
    enabled: true,
    health: "error",
    protocol: '"><svg onload=alert(1)>',
    tags: ['</button><script>alert(1)</script>'],
    categories: [1],
    lastTest: "maintenant",
    error: '<img src=x onerror=alert(1)>',
  },
];
api.renderIndexers();

const indexerRows = elements.get("#indexerRows");
assert.equal(indexerRows.children.length, 1);
const firstIndexerRow = indexerRows.children[0];
assert.equal(flattenText(indexerRows).includes("<img src=x onerror=alert(1)>"), true);
assert.equal(flattenText(indexerRows).includes("</button><script>alert(1)</script>"), true);
assert.equal(indexerRows.children.some((child) => child.tagName === "SCRIPT"), false);

api.state.busy.add("test:7");
api.state.busyLabels.set("test:7", "Test en cours...");
api.state.indexers[0].name = "Indexer actualise";
api.renderIndexers();
assert.strictEqual(indexerRows.children[0], firstIndexerRow);
assert.equal(firstIndexerRow.indexerControls.testButton.textContent, "Test en cours...");
assert.equal(firstIndexerRow.indexerControls.testButton.disabled, true);
api.state.busy.delete("test:7");
api.state.busyLabels.delete("test:7");

api.state.releaseIndexerSelection.add("7");
api.renderReleaseIndexers();
assert.equal(elements.get("#releaseIndexers").options[0].selected, true);
const preservedReleaseOption = elements.get("#releaseIndexers").options[0];
api.state.indexers[0].name = "Nom modifie sans perte de selection";
api.renderReleaseIndexers();
assert.equal(elements.get("#releaseIndexers").options[0].selected, true);
assert.notStrictEqual(elements.get("#releaseIndexers").options[0], preservedReleaseOption);
const stableReleaseOption = elements.get("#releaseIndexers").options[0];
api.renderReleaseIndexers();
assert.strictEqual(elements.get("#releaseIndexers").options[0], stableReleaseOption);
api.state.indexers.push({ ...api.state.indexers[0], id: 8, name: "Indexer masque" });
api.state.releaseIndexerSelection.add("8");
elements.get("#releaseIndexerSearch").value = "Nom modifie";
api.renderReleaseIndexers();
assert.equal(elements.get("#releaseIndexers").options.length, 1);
api.clearReleaseIndexerSelection();
assert.equal(api.state.releaseIndexerSelection.size, 0);
elements.get("#releaseIndexerSearch").value = "";
api.renderReleaseIndexers();
assert.equal(elements.get("#releaseIndexers").options.some((option) => option.selected), false);

firstIndexerRow.indexerControls.testButton.focus();
api.state.indexers = [];
api.renderIndexers();
assert.strictEqual(context.document.activeElement, elements.get("#indexerSearch"));

api.state.results = [
  {
    id: "release-1",
    title: '"><svg onload=alert(1)>',
    indexer: "</button><script>alert(1)</script>",
    category: "<img src=x onerror=alert(1)>",
    size: 1024,
    age: "1h",
    seeders: 5,
    leechers: 1,
    protocol: "torrent",
    freeleech: false,
    downloadFactor: "1",
    uploadFactor: "1",
  },
];
api.renderResults();
const resultRows = elements.get("#resultRows");
assert.equal(resultRows.children.length, 1);
assert.equal(flattenText(resultRows).includes('"><svg onload=alert(1)>'), true);
assert.equal(flattenText(resultRows).includes("</button><script>alert(1)</script>"), true);
assert.equal(resultRows.children.some((child) => child.tagName === "SVG"), false);
const firstResultRow = resultRows.children[0];
api.state.results[0].seeders = 42;
api.renderResults();
assert.strictEqual(resultRows.children[0], firstResultRow);
assert.equal(firstResultRow.grabButton.dataset.releaseId, "release-1");

api.state.partialFailures = [{ message: "Indexer A indisponible." }];
api.renderResults();
assert.equal(elements.get("#searchPartialFailures").hidden, false);

api.state.results = Array.from({ length: 205 }, (_, index) => ({
  id: `release-${index}`,
  title: `Release ${index}`,
  indexer: "Indexer",
  category: "Films",
  size: 1024,
  age: "1h",
  seeders: 1000 - index,
  leechers: 1,
  protocol: "torrent",
  freeleech: false,
  downloadFactor: "1",
  uploadFactor: "1",
}));
api.state.resultPage = 0;
api.renderResults();
assert.equal(resultRows.children.length, 80);
assert.equal(elements.get("#resultPagination").hidden, false);
const resultsStart = new FakeElement("div", ".table-scroll");
resultsStart.getBoundingClientRect = () => ({ top: 128 });
resultRows.closest = (selector) => selector === ".table-scroll" ? resultsStart : null;
api.showResultPage(1);
assert.equal(resultRows.children.length, 80);
assert.equal(api.state.resultPage, 1);
assert.equal(context.window.scrollCalls.at(-1).top, 548);
assert.equal(context.window.scrollCalls.at(-1).behavior, "auto");
api.showResultPage(1);
assert.equal(resultRows.children.length, 45);
assert.equal(api.state.resultPage, 2);
assert.equal(elements.get("#resultNext").disabled, true);
assert.equal(context.window.scrollCalls.length, 2);
api.showResultPage(1);
assert.equal(api.state.resultPage, 2);
assert.equal(context.window.scrollCalls.length, 2);
assert.match(source, /Promise\.allSettled\(activeRequests\)/);
assert.equal(
  api.searchFingerprint({ query: " ubuntu ", categories: [5000, 2000], indexerIds: [9, 7] }),
  api.searchFingerprint({ query: "ubuntu", categories: [2000, 5000], indexerIds: [7, 9] }),
);

api.state.lastGrab = { title: "Ubuntu ISO", followUrl: api.buildTorrentFollowUrl("Ubuntu ISO") };
api.renderGrabNotice();
assert.equal(elements.get("#grabNotice").hidden, false);
assert.equal(flattenText(elements.get("#grabNotice")).includes("Suivre dans Torrent Panel"), true);
assert.equal(source.includes('source", isTr4kerIndexer(indexer) ? "tr4ker" : "prowlarr"'), true);

api.applyLiveFrame({
  topic: "indexers",
  data: {
    indexers: [{ id: 99, name: "Live", enabled: true, health: "ok" }],
    refreshedAt: "2026-07-17T00:00:01Z",
  },
  collectedAt: "2099-01-01T00:00:00Z",
  stale: false,
});
assert.equal(api.state.indexers[0].id, 99);
assert.equal(api.state.sectionUpdatedAt.indexers, "2026-07-17T00:00:01Z");
api.applyLiveFrame({
  topic: "indexers",
  data: {
    indexers: [{ id: 1, name: "Stale", enabled: true, health: "ok" }],
    refreshedAt: "2026-07-17T00:00:00Z",
  },
  collectedAt: "2099-01-01T00:00:01Z",
  stale: false,
});
assert.equal(api.state.indexers[0].id, 99);
api.applyLiveFrame({
  topic: "applications",
  data: { error: { code: "offline", message: "Applications indisponibles", recovery: "Reessayer" } },
  collectedAt: new Date().toISOString(),
  stale: true,
});
assert.equal(elements.get("#appsError").hidden, false);
api.applyLiveFrame({
  topic: "health",
  data: { alerts: [{ type: "warning", message: "Live health" }], refreshedAt: "2026-07-17T00:00:02Z" },
  collectedAt: "2026-07-17T00:00:03Z",
  stale: false,
});
api.applyLiveFrame({
  topic: "history",
  data: { events: [{ title: "Live history" }], refreshedAt: "2026-07-17T00:00:04Z" },
  collectedAt: "2026-07-17T00:00:05Z",
  stale: false,
});
assert.equal(api.state.alerts[0].message, "Live health");
assert.equal(api.state.events[0].title, "Live history");
assert.match(source, /DashboardLive\.createLiveSubscription/);

const tabs = context.document.querySelectorAll(".tab");
api.setView("search", { replace: false });
assert.equal(tabs[1].attributes["aria-selected"], "true");

async function runConcurrencyChecks() {
  const payloadFor = (url) => {
    if (url.includes("/api/overview")) return { connection: "ready", indexersActive: 0, indexersError: 0 };
    if (url.includes("/api/indexers")) return { indexers: [] };
    if (url.includes("/api/applications")) return { applications: [] };
    if (url.includes("/api/health")) return { alerts: [] };
    if (url.includes("/api/history")) return { events: [] };
    return {};
  };
  const responseWith = (url) => ({
    ok: true,
    status: 200,
    json: async () => payloadFor(url),
  });

  api.state.loadPromise = null;
  api.state.searchPromise = null;
  api.state.queuedRefreshPromise = null;
  api.state.searchInFlight = false;
  api.state.searchAbort = null;

  const initialLoads = [];
  let searchCalls = [];
  context.fetch = (url, options = {}) => {
    const requestUrl = String(url);
    if (requestUrl.includes("/api/search")) {
      searchCalls.push({ url: requestUrl, options });
      return new Promise((resolve, reject) => {
        options.signal?.addEventListener("abort", () => {
          const error = new Error("aborted");
          error.name = "AbortError";
          reject(error);
        }, { once: true });
      });
    }
    return new Promise((resolve) => initialLoads.push({ url: requestUrl, resolve }));
  };

  const backgroundLoad = api.loadAll(false, { background: true });
  assert.equal(initialLoads.length, 2);
  const forcedLoad = api.loadAll(true);
  assert.equal(initialLoads.length, 2);

  initialLoads.splice(0).forEach(({ url, resolve }) => resolve(responseWith(url)));
  await backgroundLoad;
  await Promise.resolve();
  assert.equal(initialLoads.length, 2);
  assert.equal(initialLoads.filter(({ url }) => url.includes("force=true")).length, 2);

  initialLoads.splice(0).forEach(({ url, resolve }) => resolve(responseWith(url)));
  await forcedLoad;
  assert.equal(api.state.loadPromise, null);

  api.state.sectionLoaded.indexers = false;
  api.state.sectionLoaded.overview = false;
  let resolveOverview;
  let resolveIndexers;
  context.fetch = (url) => new Promise((resolve) => {
    const requestUrl = String(url);
    if (requestUrl.includes("/api/indexers")) resolveIndexers = () => resolve(responseWith(requestUrl));
    else if (requestUrl.includes("/api/overview")) resolveOverview = () => resolve(responseWith(requestUrl));
  });
  const independentLoad = api.loadAll(false, { background: true, sections: ["overview", "indexers"] });
  resolveIndexers();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(api.state.sectionLoaded.indexers, true);
  assert.equal(api.state.sectionLoaded.overview, false);
  resolveOverview();
  await independentLoad;

  let resolveStale;
  context.fetch = (url) => new Promise((resolve) => {
    resolveStale = () => resolve({
      ok: true,
      status: 200,
      json: async () => ({ indexers: [{ id: 123, name: "Before action" }] }),
    });
  });
  api.state.sectionLoaded.indexers = false;
  const staleLoad = api.loadAll(false, { background: true, sections: ["indexers"] });
  api.state.actionRevision += 1;
  resolveStale();
  await staleLoad;
  assert.equal(api.state.sectionLoaded.indexers, false);

  context.fetch = (url, options = {}) => {
    const requestUrl = String(url);
    if (!requestUrl.includes("/api/search")) return Promise.resolve(responseWith(requestUrl));
    searchCalls.push({ url: requestUrl, options });
    return new Promise((resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        reject(error);
      }, { once: true });
      searchCalls[searchCalls.length - 1].resolve = () => resolve({
        ok: true,
        status: 200,
        json: async () => ({ results: [{ id: `search-${searchCalls.length}` }], partialFailures: [] }),
      });
    });
  };
  const event = { preventDefault() {} };
  let releaseOverview;
  api.state.sectionPromises.overview = new Promise((resolve) => { releaseOverview = resolve; });
  const firstSearch = api.runSearch(event);
  const secondSearch = api.runSearch(event);
  assert.equal(searchCalls.length, 2);
  assert.equal(searchCalls[0].options.signal.aborted, true);
  searchCalls[1].resolve();
  await secondSearch;
  await firstSearch;
  assert.equal(api.state.results.length, 1);
  assert.equal(api.state.results[0].id, "search-2");
  assert.equal(api.state.searchInFlight, false);
  releaseOverview();
  delete api.state.sectionPromises.overview;
}

runConcurrencyChecks()
  .then(() => console.log("prowlarr frontend logic ok"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
