// Tests des helpers du téléchargement de dossier partagé.
// Run with: node tests/share_logic.test.js

const assert = {
  strictEqual: (a, b, msg) => { if (a !== b) throw new Error(msg || `Expected ${JSON.stringify(b)} but got ${JSON.stringify(a)}`); },
  ok: (v, msg) => { if (!v) throw new Error(msg || `Expected truthy but got ${v}`); },
};

const path = require("node:path");
const share = require(path.join(__dirname, "..", "cloud_panel", "static", "share.js"));

// --- formatBytes -----------------------------------------------------------
assert.strictEqual(share.formatBytes(0), "0 o");
assert.strictEqual(share.formatBytes(-10), "0 o");
assert.strictEqual(share.formatBytes(512), "512 o");
assert.strictEqual(share.formatBytes(1024), "1,0 Ko");
assert.strictEqual(share.formatBytes(1536), "1,5 Ko");
assert.strictEqual(share.formatBytes(5 * 1024 * 1024), "5,0 Mo");
assert.strictEqual(share.formatBytes(2.5 * 1024 ** 3), "2,5 Go");

// --- folderNameFromPath ----------------------------------------------------
assert.strictEqual(share.folderNameFromPath(""), "");
assert.strictEqual(share.folderNameFromPath("Saison 1"), "Saison 1");
assert.strictEqual(share.folderNameFromPath("Series/Show/Saison 1"), "Saison 1");
assert.strictEqual(share.folderNameFromPath("Series/Show/Saison 1/"), "Saison 1");

// --- sanitizeEntryName -----------------------------------------------------
assert.strictEqual(share.sanitizeEntryName("episode.mkv"), "episode.mkv");
assert.strictEqual(share.sanitizeEntryName("Saison 1"), "Saison 1");
assert.strictEqual(share.sanitizeEntryName(""), "");
assert.strictEqual(share.sanitizeEntryName("."), "");
assert.strictEqual(share.sanitizeEntryName(".."), "");
assert.strictEqual(share.sanitizeEntryName("a/b"), "");
assert.strictEqual(share.sanitizeEntryName("a\\b"), "");
assert.strictEqual(share.sanitizeEntryName(null), "");

// --- joinRelPath -----------------------------------------------------------
assert.strictEqual(share.joinRelPath("", "x"), "x");
assert.strictEqual(share.joinRelPath("a", "x"), "a/x");
assert.strictEqual(share.joinRelPath("a/b", "x"), "a/b/x");

// --- buildShareUrl ---------------------------------------------------------
const base = "https://dashboard.test/cloud-panel/download/tok123?password=secret";
const listing = share.buildShareUrl(base, { path: "Saison 1", list: 1, count: 0 });
assert.strictEqual(
  listing,
  "https://dashboard.test/cloud-panel/download/tok123?path=Saison+1&list=1&count=0",
  "listing URL must drop the old query and keep count=0"
);

const root = share.buildShareUrl(base, { path: "", list: 1, count: 1 });
assert.strictEqual(root, "https://dashboard.test/cloud-panel/download/tok123?list=1&count=1");

const file = share.buildShareUrl(base, { path: "a/b.mkv", dl: 1, count: 0 });
assert.strictEqual(file, "https://dashboard.test/cloud-panel/download/tok123?path=a%2Fb.mkv&dl=1&count=0");

assert.ok(!file.includes("password"), "password must never be forwarded by the downloader");

console.log("share_logic: all tests passed");
