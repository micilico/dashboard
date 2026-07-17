const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

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
    this.children.push(item);
  }

  replaceChildren(...items) {
    this.children = [];
    this.childNodes = this.children;
    this.append(...items);
    if (this.tagName === "SELECT") this.options = this.children.filter((child) => child.tagName === "OPTION");
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name];
  }

  addEventListener() {}
  focus() {}
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
    "#homeLink", "#torrentLink", "#prowlarrLink", "#indexersView", "#searchView", "#applicationsView",
    "#healthView", "#indexersSummary", "#indexersError", "#indexerRows", "#indexersEmpty", "#indexerSearch",
    "#indexerState", "#indexerProtocol", "#indexerTag", "#indexerSort", "#resetIndexers", "#testAllButton",
    "#searchForm", "#releaseQuery", "#releaseCategories", "#releaseCategoryChoices", "#releaseIndexerSearch",
    "#releaseIndexers", "#selectAllIndexers", "#clearIndexers", "#searchButton", "#searchSummary", "#resultRows",
    "#resultsEmpty", "#resultSearch", "#resultIndexer", "#resultProtocol", "#resultCategory", "#resultMaxSize",
    "#resultSort", "#appsGrid", "#appsSummary", "#appsError", "#alertsList", "#historyList", "#healthSummary",
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
      setTimeout() { return 1; },
      clearTimeout() {},
      addEventListener() {},
    },
    document: {
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
const source = fs.readFileSync("prowlarr-panel/prowlarr_panel/static/app.js", "utf8").replace(
  "init().catch(showError);",
  "globalThis.__testApi = { state, renderIndexers, renderResults, setView, handleTabKeydown };",
);
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
assert.equal(flattenText(indexerRows).includes("<img src=x onerror=alert(1)>"), true);
assert.equal(flattenText(indexerRows).includes("</button><script>alert(1)</script>"), true);
assert.equal(indexerRows.children.some((child) => child.tagName === "SCRIPT"), false);

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

const tabs = context.document.querySelectorAll(".tab");
api.setView("search", { replace: false });
assert.equal(tabs[1].attributes["aria-selected"], "true");

console.log("prowlarr frontend logic ok");
