const assert = require("node:assert/strict");
const childProcess = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

const root = path.resolve(__dirname, "..", "..");
const chromePath = process.env.CHROME_PATH || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const widths = [375, 768, 1024, 1440, 1600];

if (!fs.existsSync(chromePath)) {
  throw new Error(`Chrome executable not found: ${chromePath}`);
}

function fileUrl(file) {
  return pathToFileURL(path.join(root, file)).href;
}

function decodeHtml(value) {
  return value
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&");
}

function escapeSrcdoc(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function metricScript(name) {
  return `
    <script>
      (() => {
        const visible = (node) => {
          if (!node) return false;
          const style = getComputedStyle(node);
          const rect = node.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
        };
        const rect = (node) => {
          if (!node) return null;
          const box = node.getBoundingClientRect();
          return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height };
        };
        const nodes = Array.from(document.body.querySelectorAll("*")).filter(visible);
        const interactive = Array.from(document.querySelectorAll("a, button, input, select, summary")).filter(visible);
        const metrics = {
          fixture: ${JSON.stringify(name)},
          viewportWidth: window.innerWidth,
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          maxRight: Math.max(0, ...nodes.map((node) => rect(node).right)),
          minTouchWidth: Math.min(...interactive.map((node) => rect(node).width)),
          minTouchHeight: Math.min(...interactive.map((node) => rect(node).height)),
          smallTargets: interactive
            .map((node) => ({
              tag: node.tagName.toLowerCase(),
              className: node.className || "",
              text: (node.textContent || node.getAttribute("aria-label") || "").trim().slice(0, 48),
              width: rect(node).width,
              height: rect(node).height,
            }))
            .filter((target) => target.height < 44 || target.width < 44),
          primaryNavVisible: Array.from(document.querySelectorAll(".nav > a")).filter(visible).length,
          secondaryNavVisible: Array.from(document.querySelectorAll(".nav-more-panel a")).filter(visible).length,
          moreSummaryVisible: visible(document.querySelector(".nav-more summary")),
          morePanelVisible: visible(document.querySelector(".nav-more-panel")),
          systemStates: Array.from(document.querySelectorAll(".system-state")).map((node) => ({
            role: node.getAttribute("role"),
            height: rect(node).height,
          })),
          dataTableCells: Array.from(document.querySelectorAll(".data-table td")).map((node) => ({
            display: getComputedStyle(node).display,
            before: getComputedStyle(node, "::before").content,
          })),
          cloudActionButtons: Array.from(document.querySelectorAll("#filesView .file-table .action-btn")).filter(visible).length,
          cloudInlineActions: Array.from(document.querySelectorAll("#filesView .file-table .row-actions .button:not(.action-btn)")).filter(visible).length,
          cloudCheckboxes: Array.from(document.querySelectorAll("#filesView .file-table input[type='checkbox']")).filter(visible).length,
        };
        const pre = document.createElement("pre");
        pre.id = "metrics";
        pre.textContent = JSON.stringify(metrics);
        document.body.replaceChildren(pre);
      })();
    </script>
  `;
}

function shellFixture() {
  const navItem = (id, label, current = false) => `
    <a id="${id}" ${current ? 'aria-current="page"' : ""} href="#">
      <span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 12h16"></path></svg></span>
      <span class="nav-label">${label}</span>
    </a>`;
  return `<!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="${fileUrl("torrent-panel/torrent_panel/static/dist/app.min.css")}">
      </head>
      <body>
        <main class="shell">
          <header class="topbar">
            <div class="brand-block">
              <a class="brand-mark" href="#" aria-label="Retour à la vue d'ensemble"><svg viewBox="0 0 64 64"></svg></a>
              <div class="brand-copy"><p class="eyebrow">Centre de contrôle</p><p class="product-name">Dashboard</p></div>
            </div>
            <nav class="nav" aria-label="Navigation principale">
              ${navItem("homeLink", "Vue d'ensemble", true)}
              ${navItem("torrentLink", "Torrents")}
              ${navItem("prowlarrLink", "Prowlarr")}
              ${navItem("cloudLink", "Cloud")}
              <details class="nav-more">
                <summary aria-label="Afficher les destinations secondaires">
                  <span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h.01M12 12h.01M19 12h.01"></path></svg></span>
                  <span class="nav-label">Plus</span>
                </summary>
                <div class="nav-more-panel">
                  ${navItem("mediaLink", "Médias")}
                  ${navItem("storageLink", "Système")}
                  ${navItem("healthLink", "Santé")}
                  ${navItem("activityLink", "Activité")}
                </div>
              </details>
            </nav>
          </header>
          <section class="page-header"><div class="page-header-copy"><p class="eyebrow">Vue</p><h1>Vue d'ensemble</h1><p class="page-subtitle">Contrôle visuel.</p></div></section>
          <section class="content-panel"><div class="system-state empty" role="status"><p class="system-state-title">État vide</p></div></section>
        </main>
        ${metricScript("shell")}
      </body>
    </html>`;
}

function stateFixture() {
  return `<!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="${fileUrl("prowlarr-panel/prowlarr_panel/static/dist/app.min.css")}">
      </head>
      <body>
        <main class="shell">
          <section class="content-panel">
            <div class="system-state empty" role="status"><p class="system-state-title">Aucun résultat</p><p class="system-state-message">Essayez un autre filtre.</p></div>
            <div class="system-state unavailable" role="alert"><p class="system-state-title">Service indisponible</p><p class="system-state-message">Réessayez plus tard.</p></div>
            <div class="table-scroll">
              <table class="data-table">
                <thead><tr><th>Nom</th><th>Statut</th><th>Action</th></tr></thead>
                <tbody><tr><td class="name-cell" data-label="Nom">Indexer avec un nom suffisamment long pour vérifier le retour à la ligne</td><td data-label="Statut">Actif</td><td data-label="Action"><button class="button secondary">Tester</button></td></tr></tbody>
              </table>
            </div>
          </section>
        </main>
        ${metricScript("states")}
      </body>
    </html>`;
}

function cloudFixture() {
  const rows = Array.from({ length: 6 }, (_, index) => `
    <tr>
      <td><input type="checkbox" aria-label="Sélectionner fichier ${index + 1}"></td>
      <td><div class="file-name-cell"><span class="file-icon folder" aria-hidden="true"></span><button class="file-name dir" type="button" aria-label="Ouvrir dossier ${index + 1}">Dossier média ${index + 1}</button></div></td>
      <td>1.2 Go</td>
      <td>30/07/2026</td>
      <td>Vidéo</td>
      <td class="action-cell"><div class="row-actions"><button class="action-btn" type="button" aria-label="Actions fichier ${index + 1}">Actions</button></div></td>
    </tr>`).join("");
  return `<!doctype html>
    <html lang="fr">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <link rel="stylesheet" href="${fileUrl("cloud-panel/cloud_panel/static/dist/app.min.css")}">
      </head>
      <body>
        <main class="shell">
          <section id="filesView" class="content-panel">
            <div class="table-wrap">
              <table class="file-table">
                <thead><tr><th></th><th>Nom</th><th>Taille</th><th>Date</th><th>Type</th><th>Actions</th></tr></thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
          </section>
        </main>
        ${metricScript("cloud")}
      </body>
    </html>`;
}

const fixtures = {
  shell: shellFixture,
  states: stateFixture,
  cloud: cloudFixture,
};

function render(fixture, width) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "dashboard-visual-"));
  const file = path.join(dir, `${fixture}.html`);
  fs.writeFileSync(file, `<!doctype html>
    <meta charset="utf-8">
    <iframe id="frame" style="width:${width}px;height:900px;border:0" srcdoc="${escapeSrcdoc(fixtures[fixture]())}"></iframe>
    <script>
      frame.addEventListener("load", () => {
        const metrics = frame.contentDocument.querySelector("#metrics");
        const pre = document.createElement("pre");
        pre.id = "metrics";
        pre.textContent = metrics ? metrics.textContent : JSON.stringify({ error: "iframe metrics missing" });
        document.body.replaceChildren(pre);
      });
    </script>`, "utf8");
  const result = childProcess.spawnSync(chromePath, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--run-all-compositor-stages-before-draw",
    "--virtual-time-budget=1000",
    "--window-size=1200,1000",
    "--dump-dom",
    pathToFileURL(file).href,
  ], { encoding: "utf8", maxBuffer: 1024 * 1024 * 8 });

  if (result.status !== 0) {
    throw new Error(`Chrome failed for ${fixture} ${width}: ${result.stderr || result.stdout}`);
  }

  const match = result.stdout.match(/<pre id="metrics">([\s\S]*?)<\/pre>/);
  if (!match) {
    throw new Error(`Metrics missing for ${fixture} ${width}: ${result.stdout.slice(0, 500)}`);
  }
  return JSON.parse(decodeHtml(match[1]));
}

const results = [];
for (const fixture of Object.keys(fixtures)) {
  for (const width of widths) {
    const metrics = render(fixture, width);
    results.push(metrics);
    assert.equal(metrics.viewportWidth, width, `${fixture} viewport width should match requested breakpoint`);
    assert.ok(metrics.scrollWidth <= metrics.clientWidth + 1, `${fixture} ${width}px has horizontal overflow`);
    assert.ok(metrics.maxRight <= metrics.clientWidth + 1, `${fixture} ${width}px has visible content outside viewport`);
    assert.ok(metrics.minTouchHeight >= 44, `${fixture} ${width}px has a touch target below 44px high: ${JSON.stringify(metrics.smallTargets)}`);
  }
}

const shellMobile = results.find((item) => item.fixture === "shell" && item.viewportWidth === 375);
assert.equal(shellMobile.primaryNavVisible, 4);
assert.equal(shellMobile.moreSummaryVisible, true);
assert.equal(shellMobile.morePanelVisible, false);

const shellTablet = results.find((item) => item.fixture === "shell" && item.viewportWidth === 1024);
assert.equal(shellTablet.moreSummaryVisible, false);
assert.equal(shellTablet.secondaryNavVisible, 4);

for (const item of results.filter((entry) => entry.fixture === "states")) {
  assert.equal(item.systemStates.length, 2);
  assert.deepEqual(item.systemStates.map((state) => state.role), ["status", "alert"]);
  if (item.viewportWidth <= 767) {
    assert.ok(item.dataTableCells.every((cell) => cell.display === "grid"));
    assert.ok(item.dataTableCells.every((cell) => cell.before !== "none"));
  }
}

const cloudMobile = results.find((item) => item.fixture === "cloud" && item.viewportWidth === 375);
assert.equal(cloudMobile.cloudActionButtons, 6);
assert.equal(cloudMobile.cloudInlineActions, 0);
assert.equal(cloudMobile.cloudCheckboxes, 0);

console.log("visual breakpoints contract ok");
