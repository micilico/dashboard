const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");

function read(file) {
  return fs.readFileSync(path.join(root, file), "utf8");
}

const formsCss = read("common/css/components/forms.css");
const tablesCss = read("common/css/components/tables.css");
const tokensCss = read("common/css/tokens.css");
const utilitiesCss = read("common/css/utilities.css");
const prowlarrAppCss = read("prowlarr-panel/prowlarr_panel/static/app.css");
const prowlarrResponsiveCss = read("prowlarr-panel/prowlarr_panel/static/css/responsive.css");
const torrentTorrentsCss = read("torrent-panel/torrent_panel/static/css/torrents.css");
const torrentHomeCss = read("torrent-panel/torrent_panel/static/css/home.css");
const torrentResponsiveCss = read("torrent-panel/torrent_panel/static/css/responsive.css");
const torrentConsoleCss = read("torrent-panel/torrent_panel/static/console.css");
const torrentConsoleJs = read("torrent-panel/torrent_panel/static/console.js");

assert.match(formsCss, /\.filters,\s*\.search-form\s*\{/);
assert.match(formsCss, /@media \(max-width: 1180px\)[\s\S]*\.filters, \.search-form/);
assert.match(tablesCss, /@media \(max-width: 767px\)[\s\S]*\.data-table td::before/);
assert.match(utilitiesCss, /\.split\s*\{[\s\S]*grid-template-columns: 7fr 5fr/);
assert.match(utilitiesCss, /\.card-grid\s*\{[\s\S]*repeat\(2, minmax\(0, 1fr\)\)/);
assert.equal(/--space-(6|10|20)([^0-9]|$)/.test(tokensCss), false);

assert.equal(/\.filters,\s*\.search-form\s*\{[\s\S]*border-radius: var\(--radius-panel\)/.test(prowlarrAppCss), false);
assert.equal(/\.data-table, \.data-table thead/.test(prowlarrResponsiveCss), false);
assert.equal(/\.data-table td::before/.test(prowlarrResponsiveCss), false);
assert.equal(/\.panel-head, \.filters, \.search-form/.test(prowlarrResponsiveCss), false);
assert.equal(/\.filters\s*\{[\s\S]*border-radius: var\(--radius-panel\)/.test(torrentTorrentsCss), false);
assert.equal(/\.form-grid,\s*\.filters/.test(torrentHomeCss), false);
assert.equal(/\.form-grid,\s*[\r\n\s]*\.filters/.test(torrentResponsiveCss), false);
assert.equal(/\.filters\s*\{\s*margin:/.test(torrentResponsiveCss), false);
assert.equal(/\.table-wrap table\s*\{/.test(torrentConsoleCss), false);
assert.equal(/innerHTML/.test(torrentConsoleJs), false);
assert.match(torrentConsoleJs, /className: "data-table"/);
assert.match(torrentConsoleJs, /systemState\(\{ type: "empty"/);

console.log("ui patterns contract ok");
