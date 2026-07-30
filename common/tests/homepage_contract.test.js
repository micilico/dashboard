const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..", "..");

function read(file) {
  return fs.readFileSync(path.join(root, file), "utf8");
}

const settings = read("homepage/settings.yaml");
const services = read("homepage/services.yaml");
const widgets = read("homepage/widgets.yaml");
const customCss = read("homepage/custom.css");
const customJs = read("homepage/custom.js");

assert.match(settings, /title: Centre de contrôle/);
assert.match(settings, /Accès rapides:/);
assert.match(services, /- Accès rapides:/);
assert.match(services, /- Cloud:/);
assert.match(services, /- Montage média:/);
assert.match(widgets, /label: Stockage média/);

const descriptions = services
  .split("\n")
  .filter((line) => line.trim().startsWith("description:"))
  .join("\n");
assert.doesNotMatch(descriptions, /(tunnel|VPS|ultra\.cc|\/mnt|\brc\b)/i);
assert.doesNotMatch(services, /label: (Transfers|Speed|Bytes|Errors)/);

assert.match(customCss, /--dashboard-bg: #07080b/);
assert.match(customCss, /\.dashboard-homepage-ready \.dashboard-launcher-link/);
assert.match(customCss, /@media \(max-width: 767px\)/);
assert.match(customCss, /prefers-reduced-motion/);

assert.match(customJs, /document\.title = "Centre de contrôle"/);
assert.match(customJs, /rel\.add\("noopener"\)/);
assert.match(customJs, /rel\.add\("noreferrer"\)/);
assert.match(customJs, /MutationObserver/);
assert.doesNotMatch(customJs, /innerHTML/);

console.log("homepage contract ok");
