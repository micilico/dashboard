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
const sortHeads = ["name", "state", "progress", "downloadSpeed", "uploadSpeed", "ratio", "size", "eta"].map((key) => {
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
vm.runInNewContext(
  `${source}
globalThis.__testApi = { formatBytes, formatSpeed, formatRatio, formatEta, stateMeta, filteredTorrents, state };`,
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

api.state.torrents = [
  { hash: "a", name: "Ubuntu ISO", state: "downloading", downloadSpeed: 200, progress: 0.3, tags: "linux", category: "Images" },
  { hash: "b", name: "Archive", state: "stalledDL", downloadSpeed: 0, progress: 0.1, tags: "backup", category: "Docs" },
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

console.log("frontend logic ok");
