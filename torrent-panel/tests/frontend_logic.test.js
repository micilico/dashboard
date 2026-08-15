const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function createElement(selector = "") {
  return {
    selector,
    children: [],
    dataset: {},
    className: "",
    textContent: "",
    value: "",
    checked: false,
    hidden: false,
    style: {},
    classList: {
      add() {},
      remove() {},
    },
    append(...items) {
      this.children.push(...items);
    },
    replaceChildren(...items) {
      this.children = items;
    },
    setAttribute(name, value) {
      this[name] = value;
    },
    addEventListener() {},
    focus() {},
    showModal() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
  };
}

const elements = new Map();
const sortHeads = ["name", "state", "progress", "downloadSpeed", "uploadSpeed", "ratio", "size", "eta", "addedOn"].map((key) => {
  const el = createElement(".sort-head");
  el.dataset.sort = key;
  el.textContent = key;
  return el;
});

const context = {
  console,
  setTimeout(fn) {
    return fn ? 1 : 0;
  },
  clearTimeout() {},
  setInterval() {
    return 1;
  },
  clearInterval() {},
  window: {
    setTimeout(fn) {
      return fn ? 1 : 0;
    },
    clearTimeout() {},
    setInterval() {
      return 1;
    },
    clearInterval() {},
  },
  localStorage: {
    data: {},
    getItem(key) {
      return this.data[key] || null;
    },
    setItem(key, value) {
      this.data[key] = value;
    },
  },
  navigator: { clipboard: { writeText() {} } },
  document: {
    hidden: false,
    activeElement: createElement("active"),
    createElement,
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, createElement(selector));
      return elements.get(selector);
    },
    querySelectorAll(selector) {
      return selector === ".sort-head" ? sortHeads : [];
    },
    addEventListener() {},
  },
  fetch: async (path) => ({
    ok: true,
    json: async () => (path.includes("session") ? { csrfToken: "token" } : { torrents: [] }),
  }),
};

const source = fs.readFileSync("torrent-panel/torrent_panel/static/app.js", "utf8");
const commonApiSource = fs.readFileSync("common/js/api.js", "utf8");
const consoleSource = fs.readFileSync("torrent-panel/torrent_panel/static/console.js", "utf8");
const overviewHtml = fs.readFileSync("torrent-panel/torrent_panel/static/index.html", "utf8");

assert.equal(overviewHtml.includes("Votre espace média"), false);
assert.equal(overviewHtml.includes("overview-storage-card"), false);
assert.equal(overviewHtml.includes("storageVisualization"), false);
assert.match(overviewHtml, /id="overviewMetrics"[\s\S]+class="overview-lower-grid"/);
assert.match(overviewHtml, /class="selection-toolbar"[\s\S]+id="selectVisible"[\s\S]+class="torrent-table"/);
assert.match(overviewHtml, /data-sort="addedOn"/);
assert.match(overviewHtml, /data-sort="uploaded"/);
assert.match(overviewHtml, /id="densityToggle"/);
assert.match(overviewHtml, /id="sortDirectionSelect"/);
assert.match(overviewHtml, /id="tr4kerTrackerInput"/);
assert.match(overviewHtml, /id="organizeMediaButton"/);
assert.match(overviewHtml, /id="organizeMediaDialog"/);
assert.match(source, /\/api\/torrents\/organize-preview/);
assert.match(source, /\/api\/torrents\/organize/);
assert.match(source, /Les torrents restent en pause jusqu’à leur vérification à 100 %/);
assert.match(source, /entry\.currentPath.*entry\.targetPath/);
assert.match(source, /operation\.oldPath.*operation\.newPath/);
assert.equal(source.includes("renderStorageCard"), false);
assert.equal(source.includes("storageVisualization"), false);
assert.equal(source.includes("Operationnel"), false);
assert.equal(source.includes("form.innerHTML"), false);
assert.equal(source.includes("row.innerHTML"), false);
assert.equal(consoleSource.includes("innerHTML"), false);
assert.match(consoleSource, /function seriesLineChart/);
assert.equal(consoleSource.includes("function barChart"), false);
assert.equal(consoleSource.includes("function lineChart"), false);
assert.match(consoleSource, /chart-guide/);
assert.match(consoleSource, /chart-tooltip/);

// Refonte : Uploadé, densité, surlignage, undo toast, fraîcheur
assert.ok(source.includes('uploaded: "Total téléversé"'), "SORT_LABELS must expose uploaded");
assert.ok(source.includes("function showUndoToast"), "undo toast helper required");
assert.ok(source.includes("function formatFreshness"), "freshness helper required");
assert.ok(source.includes('className: "search-hit"'), "search highlight must be marked");
assert.ok(source.includes("showUndoToast("), "pause must offer an undo action");
const torrentsCss = fs.readFileSync("torrent-panel/torrent_panel/static/css/torrents.css", "utf8");
assert.ok(torrentsCss.includes(".filters.is-stuck"), "sticky filter bar needs a stuck state");
assert.ok(torrentsCss.includes(".torrents-panel.compact"), "compact density styles required");
assert.ok(torrentsCss.includes(".toast-action"), "undo toast button styles required");
assert.ok(torrentsCss.includes("mark.search-hit"), "search highlight styles required");
assert.match(consoleSource, /function panel\(title, subtitle, bodyChildren/);
assert.match(consoleSource, /systemState\(\{ type: "empty"/);
assert.match(consoleSource, /className: "table-wrap table-scroll"/);
assert.match(source, /function createSystemState/);
assert.match(source, /Services indisponibles/);
assert.match(source, /Aucune activité récente/);
assert.match(source, /metric-card\$\{card\.available \? "" : " unavailable"\}/);
assert.equal(source.includes('empty.className = "activity-empty"'), false);

const overviewCss = fs.readFileSync("torrent-panel/torrent_panel/static/css/home.css", "utf8");
assert.equal(overviewCss.includes("min-height: 350px"), false);
assert.match(overviewCss, /\.services-list \.system-state/);
assert.match(overviewCss, /\.metric-card\.unavailable/);

vm.runInNewContext(
  `${commonApiSource}
${source}
globalThis.__testApi = { formatBytes, formatSpeed, formatRatio, formatEta, stateMeta, filteredTorrents, renderFollowNotice, renderSelection, formatFreshness, showUndoToast, state, els };`,
  context,
);

const api = context.__testApi;

assert.equal(api.formatBytes(0), "0 o");
assert.equal(api.formatBytes(1536), "1.5 Ko");
assert.equal(api.formatSpeed(1024), "1.0 Ko/s");
assert.equal(api.formatRatio(1.234), "1.23");
assert.equal(api.formatEta(3660), "1 h 01");
assert.equal(api.stateMeta({ state: "stalledDL", progress: 0.4 }).group, "error");
assert.equal(api.stateMeta({ state: "metaDL", progress: 0.1 }).text, "Métadonnées");
assert.equal(api.stateMeta({ state: "queuedUP", progress: 1 }).text, "En attente de partage");
assert.equal(api.formatFreshness(null), "");
assert.equal(api.formatFreshness(new Date(Date.now() - 15000)), "il y a 15 s");
assert.equal(api.formatFreshness(new Date(Date.now() - 5000)), "à l'instant");
assert.equal(typeof api.showUndoToast, "function");

api.state.torrents = [
  { hash: "a", name: "Ubuntu ISO", state: "downloading", downloadSpeed: 200, progress: 0.3, tags: "linux", category: "Images", addedOn: 20 },
  { hash: "b", name: "Archive", state: "stalledDL", downloadSpeed: 0, progress: 0.1, tags: "backup", category: "Docs", addedOn: 10 },
];
api.state.prefs.search = "archive";
api.state.prefs.status = "all";
api.state.prefs.category = "all";
api.state.prefs.tag = "all";
api.state.prefs.sort = "default";
assert.equal(api.filteredTorrents().length, 1);
assert.equal(api.filteredTorrents()[0].hash, "b");

api.state.prefs.search = "";
assert.equal(api.filteredTorrents()[0].hash, "b");
api.state.prefs.sort = "addedOn";
api.state.prefs.direction = "desc";
assert.equal(api.filteredTorrents()[0].hash, "a");
api.state.prefs.direction = "asc";
assert.equal(api.filteredTorrents()[0].hash, "b");
api.state.prefs.sort = "default";
api.state.prefs.direction = "desc";
assert.equal(api.filteredTorrents()[0].hash, "a");

api.state.selected.add("a");
api.renderSelection(api.state.torrents);
assert.equal(api.els.selectVisible.indeterminate, true);
assert.equal(api.els.visibleSelectionSummary.textContent, "1 sur 2 sélectionné");

api.state.sourceHint = "prowlarr";
api.state.pendingReleaseTitle = "Ubuntu ISO";
api.renderFollowNotice();
assert.equal(api.els.followNotice.hidden, false);
assert.equal(api.els.followNotice.children.length > 0, true);

// ————— Graphique SVG : tokens et rendu —————

const chartCss = fs.readFileSync("torrent-panel/torrent_panel/static/console.css", "utf8");
const tokenCss = fs.readFileSync("common/css/tokens.css", "utf8");
assert.equal(chartCss.includes("--text-muted"), false, "invalid token --text-muted must not be used");
assert.equal(chartCss.includes("--surface-1"), false, "invalid token --surface-1 must not be used");
const definedTokens = new Set([...tokenCss.matchAll(/--[a-z0-9-]+:/g)].map((m) => m[0].slice(0, -1)));
const usedTokens = new Set([...chartCss.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]));
for (const used of usedTokens) {
  if (used === "--arrow-x") continue; // variable locale définie dynamiquement (fallback fourni)
  assert.ok(definedTokens.has(used), `token ${used} must be defined in tokens.css`);
}

function chartElement(tag = "") {
  return {
    tag,
    children: [],
    dataset: {},
    className: "",
    class: "",
    textContent: "",
    value: "",
    hidden: false,
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {} },
    append(...items) {
      this.children.push(...items);
    },
    replaceChildren(...items) {
      this.children = items;
    },
    setAttribute(name, value) {
      this[name] = String(value);
    },
    getBoundingClientRect() {
      return { left: 0, top: 0, width: 480, height: 260 };
    },
    addEventListener() {},
  };
}

const chartContext = {
  console,
  setTimeout(fn) {
    return fn ? 1 : 0;
  },
  clearTimeout() {},
  setInterval() {
    return 1;
  },
  clearInterval() {},
  navigator: { clipboard: { writeText() {} } },
  document: {
    createElement: (t) => chartElement(t),
    createElementNS: (ns, t) => chartElement(t),
    querySelector: () => chartElement(),
    querySelectorAll: () => [],
    addEventListener() {},
  },
};
chartContext.window = {
  DashboardDOM: null,
  DashboardNavigation: { configure() {} },
  setTimeout(fn) {
    return fn ? 1 : 0;
  },
  clearTimeout() {},
  setInterval() {
    return 1;
  },
  clearInterval() {},
};
vm.runInNewContext(
  `${fs.readFileSync("common/js/api.js", "utf8")}
${fs.readFileSync("common/js/dom.js", "utf8")}
window.__DASHBOARD_CONSOLE_CONFIG__ = { section: "stats", apiPrefix: "/api", publicPrefix: "/stats-panel" };
${consoleSource}
globalThis.__chartTest = { seriesLineChart, chartEmptyState };`,
  chartContext,
);
const chartApi = chartContext.__chartTest;
const chartSeries = [
  { key: "downloaded", label: "Download", format: (v) => `${v} o`, className: "series-a" },
  { key: "uploaded", label: "Upload", format: (v) => `${v} o`, className: "series-b" },
];
const makeDay = (date, values) => Object.assign({ date, ratio: 0.4, diskUsedPercent: 0 }, values);
const collectText = (root) =>
  (root.textContent || "") + (root.children || []).map(collectText).join("");

// Aucune donnée → état vide dédié
const emptyBox = chartApi.seriesLineChart([], chartSeries, { title: "Volumes échangés" });
assert.ok(emptyBox, "empty input must return a box");
assert.match(emptyBox.className, /chart-box-empty/);
assert.ok(collectText(emptyBox).includes("Aucune donnée"), "empty state message required");

// Un seul jour → état vide informatif (pas de courbe trompeuse)
const oneDayBox = chartApi.seriesLineChart([makeDay("2026-08-06", { downloaded: 10, uploaded: 5 })], chartSeries, { title: "Volumes échangés" });
assert.match(oneDayBox.className, /chart-box-empty/);
assert.ok(collectText(oneDayBox).includes("1 jour observé"), "single-day state must mention observed count");

// Plusieurs jours → SVG complet avec titre, période, légende
const days = [];
const start = new Date("2026-07-08T00:00:00");
for (let i = 0; i < 30; i += 1) {
  const d = new Date(start);
  d.setDate(d.getDate() + i);
  days.push(makeDay(d.toISOString().slice(0, 10), { downloaded: i * 10, uploaded: i * 3 }));
}
const fullBox = chartApi.seriesLineChart(days, chartSeries, { title: "Volumes échangés" });
assert.equal(fullBox.className, "chart-box");
const svgNode = fullBox.children.find((n) => n.tag === "svg");
assert.ok(svgNode, "chart box must contain an svg");
const svgTags = svgNode.children.map((n) => n.tag);
for (const expected of ["desc", "defs", "path", "circle", "rect", "text", "line"]) {
  assert.ok(svgTags.includes(expected), `svg must include <${expected}>`);
}
const linePath = svgNode.children.find((n) => n.tag === "path" && String(n.class || n.className).includes("chart-line-path"));
assert.match(linePath.d, /C /, "curves must be smoothed with bezier commands");
const gradientStops = svgNode.children.find((n) => n.tag === "defs").children.flatMap((g) => g.children);
assert.equal(gradientStops.length, 4, "two series must produce two gradients with two stops each");
assert.ok(gradientStops.every((s) => String(s.class || s.className).includes("chart-gradient-stop")), "gradient stops must be styled by class");

const head = fullBox.children.find((n) => String(n.className || "").includes("chart-head"));
assert.ok(head.children.some((n) => n.textContent === "Volumes échangés"), "chart title required");
assert.ok(head.children.some((n) => n.textContent.includes("30 derniers jours")), "30-day period label required");
const legend = fullBox.children.find((n) => String(n.className || "").includes("chart-legend"));
assert.equal(legend.children.length, 2, "legend must list each series");

// Période réelle quand la fenêtre est plus courte que 30 jours
const shortBox = chartApi.seriesLineChart(days.slice(0, 7), chartSeries, { title: "Volumes échangés" });
const shortHead = shortBox.children.find((n) => String(n.className || "").includes("chart-head"));
assert.ok(shortHead.children.some((n) => n.textContent.includes("Du ")), "shorter period must show a date range");

// Valeurs nulles ou égales à zéro → le graphique reste rendu
const zeroDays = days.map((d, i) => makeDay(d.date, i % 2 ? { downloaded: 0, uploaded: 0 } : { downloaded: 100, uploaded: 50 }));
const zeroBox = chartApi.seriesLineChart(zeroDays, chartSeries, { title: "Volumes échangés" });
assert.equal(zeroBox.className, "chart-box", "zero values must still render a chart");

console.log("chart svg ok");

console.log("frontend logic ok");
