// Tests des helpers du téléchargement de dossier partagé.
// Run with: node tests/share_logic.test.js

const assert = {
  strictEqual: (a, b, msg) => { if (a !== b) throw new Error(msg || `Expected ${JSON.stringify(b)} but got ${JSON.stringify(a)}`); },
  ok: (v, msg) => { if (!v) throw new Error(msg || `Expected truthy but got ${v}`); },
  deepEqual: (a, b, msg) => { const sa = JSON.stringify(a), sb = JSON.stringify(b); if (sa !== sb) throw new Error(msg || `Expected ${sb} but got ${sa}`); },
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

// --- normalisation des collisions -----------------------------------------
assert.strictEqual(share.normalizedEntryKey("Episode.MKV"), "episode.mkv");
assert.strictEqual(share.normalizedEntryKey("É"), share.normalizedEntryKey("E\u0301"));

// --- collectPlan -----------------------------------------------------------
// Plain async functions with no DOM dependency: stub fetch and exercise the
// tree walk, symlink/collision skipping, empty directories and count=1/0.
async function testCollectPlan() {
  const tree = {
    "": [
      { name: "a.txt", path: "a.txt", is_dir: false, size_bytes: 5 },
      { name: "sub", path: "sub", is_dir: true },
      { name: "alias", path: "alias", is_dir: true, is_symlink: true },
      { name: "A.txt", path: "A.txt", is_dir: false, size_bytes: 7 },
    ],
    sub: [
      { name: "b.txt", path: "sub/b.txt", is_dir: false, size_bytes: 10 },
      { name: "empty", path: "sub/empty", is_dir: true },
    ],
    "sub/empty": [],
  };
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    const parsed = new URL(String(url));
    calls.push(parsed);
    const path = parsed.searchParams.get("path") || "";
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({ items: tree[path] || [] }),
    };
  };
  try {
    const plan = await share.collectPlan(
      "https://dashboard.test/cloud-panel/download/tok123",
      "",
      null,
      () => {}
    );
    assert.deepEqual(plan.files.map((f) => f.relative), ["a.txt", "sub/b.txt"]);
    assert.deepEqual(plan.dirs, ["sub", "sub/empty"]);
    assert.strictEqual(plan.totalBytes, 15);
    assert.deepEqual(
      plan.skipped.map((s) => s.reason),
      ["lien symbolique ignoré", "conflit de casse ou d'Unicode"]
    );
    assert.strictEqual(calls[0].searchParams.get("count"), "1");
    assert.ok(calls.length >= 3);
    assert.ok(
      calls.slice(1).every((call) => call.searchParams.get("count") === "0"),
      "only the root listing may increment the share counter"
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
}

testCollectPlan()
  .then(() => console.log("share_logic: all tests passed"))
  .catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
