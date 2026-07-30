const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");
const navOrder = ["home", "torrent", "prowlarr", "cloud", "media", "storage", "health", "activity"];
const navIds = ["homeLink", "torrentLink", "prowlarrLink", "cloudLink", "mediaLink", "storageLink", "healthLink", "activityLink"];
const navLabels = ["Vue d’ensemble", "Torrents", "Prowlarr", "Cloud", "Médias", "Système", "Santé", "Activité"];
const htmlFiles = [
  "torrent-panel/torrent_panel/static/index.html",
  "torrent-panel/torrent_panel/static/activity.html",
  "torrent-panel/torrent_panel/static/storage.html",
  "torrent-panel/torrent_panel/static/media.html",
  "torrent-panel/torrent_panel/static/health.html",
  "prowlarr-panel/prowlarr_panel/static/index.html",
  "cloud-panel/cloud_panel/static/index.html",
];

function read(file) {
  return fs.readFileSync(path.join(root, file), "utf8");
}

function navBlock(html, file) {
  assert.ok(html.includes("<!-- dashboard-shell:start"), `${file}: shell must be generated from common/shell.py`);
  assert.ok(html.includes('<p class="product-name">Dashboard</p>'), `${file}: brand name must not be the page h1`);
  assert.equal((html.match(/<header class="topbar">/g) || []).length, 1, `${file}: expected one shell header`);
  const match = html.match(/<nav class="nav" aria-label="Navigation principale">([\s\S]*?)<\/nav>/);
  assert.ok(match, `${file}: missing global nav`);
  return match[1];
}

for (const file of htmlFiles) {
  const html = read(file);
  const nav = navBlock(html, file);
  const keys = [...nav.matchAll(/data-shell-nav="([^"]+)"/g)].map((match) => match[1]);
  const ids = [...nav.matchAll(/<a id="([^"]+)"/g)].map((match) => match[1]);
  const labels = [...nav.matchAll(/<a id="[^"]+"[\s\S]*?<span class="nav-label">([^<]+)<\/span>/g)].map((match) => match[1]);
  assert.deepEqual(keys, navOrder, `${file}: global nav order changed`);
  assert.deepEqual(ids, navIds, `${file}: global nav ids must match the Prowlarr shell`);
  assert.deepEqual(labels, navLabels, `${file}: global nav labels changed`);
  assert.ok(nav.includes('id="cloudLink"'), `${file}: Cloud nav item is required`);
  assert.ok(nav.includes('<details class="nav-more">'), `${file}: secondary mobile destinations must live in Plus`);
  assert.equal((nav.match(/<summary aria-label="Afficher les destinations secondaires">/g) || []).length, 1, `${file}: expected one Plus summary`);
  assert.equal((nav.match(/<svg viewBox="0 0 24 24"/g) || []).length, navOrder.length + 1, `${file}: every nav item plus the Plus control needs an icon`);
  assert.equal((html.match(/aria-current="page"/g) || []).length, 1, `${file}: expected exactly one current page`);
  assert.equal((html.match(/<h1\b/g) || []).length, 1, `${file}: expected exactly one destination h1`);
  const brandCopy = html.match(/<div class="brand-copy">([\s\S]*?)<\/div>/);
  assert.ok(brandCopy, `${file}: expected brand copy`);
  assert.equal(/<h1\b/.test(brandCopy[1]), false, `${file}: brand must not own document hierarchy`);
}

for (const file of [
  "torrent-panel/torrent_panel/static/css/home.css",
  "cloud-panel/cloud_panel/static/app.css",
  "prowlarr-panel/prowlarr_panel/static/app.css",
]) {
  const css = read(file);
  assert.equal(/\.sidebar-footer\s*\{[^}]*display\s*:\s*grid/.test(css), false, `${file}: local sidebar footer display override`);
}

assert.equal(fs.existsSync(path.join(root, "prowlarr-panel/prowlarr_panel/static/css/z-sidebar-lock.css")), false);

console.log("shell contract ok");
