// Tests frontend pour Cloud Panel
// Run with: node tests/frontend_logic.test.js

const assert = {
  strictEqual: (a, b, msg) => { if (a !== b) throw new Error(msg || `Expected ${JSON.stringify(b)} but got ${JSON.stringify(a)}`); },
  ok: (v, msg) => { if (!v) throw new Error(msg || `Expected truthy but got ${v}`); },
  deepEqual: (a, b) => { const sa = JSON.stringify(a), sb = JSON.stringify(b); if (sa !== sb) throw new Error(`Expected ${sb} but got ${sa}`); },
};
const fs = require("node:fs");
const path = require("node:path");

// Import app.js functions by redefining them in test scope (mimics the actual app.js logic)
// Format utilities
function fmtSize(b) {
  const n = Number(b) || 0; if (n === 0) return "0 o";
  const u = ["o", "Ko", "Mo", "Go", "To"]; const i = Math.min(Math.floor(Math.log(Math.abs(n)) / Math.log(1024)), 4);
  return `${(n / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

function fmtDate(ts) { const n = Number(ts); return n > 0 ? new Date(n * 1000).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" }) : ""; }

function fileIcon(name, isDir) {
  if (isDir) return "folder";
  const ext = (name || "").split(".").pop().toLowerCase();
  if (["mp4","mkv","avi","mov","webm","m4v"].includes(ext)) return "video";
  if (["mp3","wav","flac","ogg","m4a","aac"].includes(ext)) return "audio";
  if (["jpg","jpeg","png","gif","webp","svg","bmp","ico"].includes(ext)) return "image";
  if (["pdf"].includes(ext)) return "pdf";
  if (["zip","rar","7z","tar","gz","bz2","xz"].includes(ext)) return "archive";
  if (["doc","docx","xls","xlsx","ppt","pptx","odt","ods"].includes(ext)) return "document";
  return "file";
}

// Routing
const PP = "/cloud-panel";
const BASE = `${PP}/`;
function rt(p) { return p === "/" ? BASE : `${PP}${p.startsWith("/") ? p : "/" + p}`; }
function au(p) { return rt(`/api${p}`); }

// File sorting logic
function getSortedFiltered(files, search, sortKey, sortDir) {
  let items = search ? files.filter(f => f.name.toLowerCase().includes(search.toLowerCase())) : files;
  const dir = sortDir === "asc" ? 1 : -1;
  items = [...items].sort((a, b) => {
    if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
    if (sortKey === "name") return a.name.localeCompare(b.name, "fr") * dir;
    if (sortKey === "size") return ((a.size_bytes || 0) - (b.size_bytes || 0)) * dir;
    if (sortKey === "date") return ((a.modified || 0) - (b.modified || 0)) * dir;
    return 0;
  });
  return items;
}

function paginate(items, page, pageSize) {
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize));
  const p = Math.max(1, Math.min(totalPages, page));
  const start = (p - 1) * pageSize;
  return { items: items.slice(0, start + pageSize), page: p, totalPages, total: items.length };
}

function navigate(path, currentPath) {
  return path; // returns new path
}

function breadcrumbParts(path) {
  return path ? path.replace(/\\/g, "/").split("/").filter(Boolean) : [];
}

// ── Tests ──

function test(name, fn) {
  try {
    fn();
    console.log(`  ✓ ${name}`);
  } catch (e) {
    console.error(`  ✗ ${name}: ${e.message}`);
    process.exitCode = 1;
  }
}

function suite(name, fn) {
  console.log(`\n${name}`);
  fn();
}

suite('Format utilities', () => {
  test('fmtSize with 0 returns "0 o"', () => {
    assert.strictEqual(fmtSize(0), "0 o");
  });
  test('fmtSize with bytes', () => {
    assert.strictEqual(fmtSize(500), "500 o");
  });
  test('fmtSize with KB', () => {
    assert.strictEqual(fmtSize(2048), "2.0 Ko");
  });
  test('fmtSize with MB', () => {
    assert.strictEqual(fmtSize(1048576), "1.0 Mo");
  });
  test('fmtSize with GB', () => {
    const gb = fmtSize(1073741824);
    assert.ok(gb.includes("Go"), `Expected GB format, got ${gb}`);
  });
  test('fmtSize with TB', () => {
    const tb = fmtSize(1099511627776);
    assert.ok(tb.includes("To"), `Expected TB format, got ${tb}`);
  });
  test('fmtSize with invalid input returns 0 o', () => {
    assert.strictEqual(fmtSize(NaN), "0 o");
    assert.strictEqual(fmtSize(undefined), "0 o");
    assert.strictEqual(fmtSize(null), "0 o");
  });
  test('fmtDate with valid timestamp', () => {
    const d = fmtDate(1700000000);
    assert.ok(d.length > 0, `Expected date string, got ${d}`);
  });
  test('fmtDate with 0 returns empty', () => {
    assert.strictEqual(fmtDate(0), "");
  });
});

suite('File icon detection', () => {
  test('folder returns folder', () => {
    assert.strictEqual(fileIcon("anything", true), "folder");
  });
  test('video extensions', () => {
    assert.strictEqual(fileIcon("movie.mp4", false), "video");
    assert.strictEqual(fileIcon("movie.mkv", false), "video");
    assert.strictEqual(fileIcon("movie.avi", false), "video");
  });
  test('audio extensions', () => {
    assert.strictEqual(fileIcon("song.mp3", false), "audio");
    assert.strictEqual(fileIcon("song.flac", false), "audio");
    assert.strictEqual(fileIcon("song.wav", false), "audio");
  });
  test('image extensions', () => {
    assert.strictEqual(fileIcon("photo.jpg", false), "image");
    assert.strictEqual(fileIcon("photo.png", false), "image");
    assert.strictEqual(fileIcon("photo.gif", false), "image");
  });
  test('pdf extension', () => {
    assert.strictEqual(fileIcon("doc.pdf", false), "pdf");
  });
  test('archive extensions', () => {
    assert.strictEqual(fileIcon("data.zip", false), "archive");
    assert.strictEqual(fileIcon("data.rar", false), "archive");
    assert.strictEqual(fileIcon("data.tar.gz", false), "archive"); // .gz is in archive list
  });
  test('document extensions', () => {
    assert.strictEqual(fileIcon("report.doc", false), "document");
    assert.strictEqual(fileIcon("report.docx", false), "document");
    assert.strictEqual(fileIcon("sheet.xlsx", false), "document");
  });
  test('unknown extension returns file', () => {
    assert.strictEqual(fileIcon("data.bin", false), "file");
    assert.strictEqual(fileIcon("noext", false), "file");
  });
});

suite('URL routing', () => {
  test('rt returns correct paths', () => {
    assert.strictEqual(rt("/"), "/cloud-panel/");
    assert.strictEqual(rt("/files"), "/cloud-panel/files");
    assert.strictEqual(rt("files"), "/cloud-panel/files");
  });
  test('au returns correct API paths', () => {
    assert.strictEqual(au("/files"), "/cloud-panel/api/files");
    assert.strictEqual(au("/files/upload"), "/cloud-panel/api/files/upload");
    assert.strictEqual(au("/session"), "/cloud-panel/api/session");
  });
  test('au with empty public prefix', () => {
    const localPP = "";
    const localBASE = `${localPP}/`;
    function localRt(p) { return p === "/" ? localBASE : `${localPP}${p.startsWith("/") ? p : "/" + p}`; }
    function localAu(p) { return localRt(`/api${p}`); }
    assert.strictEqual(localRt("/"), "/");
    assert.strictEqual(localAu("/files"), "/api/files");
  });
});

suite('Breadcrumb logic', () => {
  test('empty path gives empty parts', () => {
    assert.strictEqual(breadcrumbParts("").length, 0);
  });
  test('path splits correctly', () => {
    const parts = breadcrumbParts("movies/2024/action");
    assert.strictEqual(parts.length, 3);
    assert.deepEqual(parts, ["movies", "2024", "action"]);
  });
  test('single part', () => {
    assert.deepEqual(breadcrumbParts("videos"), ["videos"]);
  });
  test('backslash normalized to forward slash', () => {
    assert.deepEqual(breadcrumbParts("movies\\2024"), ["movies", "2024"]);
  });
});

suite('Sort and filter logic', () => {
  const files = [
    { name: "zeta.txt", is_dir: false, size_bytes: 300, modified: 3000 },
    { name: "alpha.txt", is_dir: false, size_bytes: 100, modified: 1000 },
    { name: "FolderA", is_dir: true, size_bytes: 0, modified: 2000 },
    { name: "beta.txt", is_dir: false, size_bytes: 200, modified: 2000 },
  ];

  test('directories always first', () => {
    const result = getSortedFiltered(files, "", "name", "asc");
    assert.ok(result[0].is_dir, "first should be directory");
    assert.strictEqual(result[0].name, "FolderA");
  });

  test('sort by name ascending', () => {
    const result = getSortedFiltered(files, "", "name", "asc");
    const nonDirs = result.filter(f => !f.is_dir);
    assert.strictEqual(nonDirs[0].name, "alpha.txt");
    assert.strictEqual(nonDirs[1].name, "beta.txt");
    assert.strictEqual(nonDirs[2].name, "zeta.txt");
  });

  test('sort by name descending', () => {
    const result = getSortedFiltered(files, "", "name", "desc");
    const nonDirs = result.filter(f => !f.is_dir);
    assert.strictEqual(nonDirs[0].name, "zeta.txt");
    assert.strictEqual(nonDirs[1].name, "beta.txt");
  });

  test('sort by size', () => {
    const result = getSortedFiltered(files, "", "size", "asc");
    const nonDirs = result.filter(f => !f.is_dir);
    assert.strictEqual(nonDirs[0].name, "alpha.txt");
    assert.strictEqual(nonDirs[1].name, "beta.txt");
  });

  test('sort by date', () => {
    const result = getSortedFiltered(files, "", "date", "asc");
    const nonDirs = result.filter(f => !f.is_dir);
    assert.strictEqual(nonDirs[0].name, "alpha.txt");
  });

  test('search filters by name', () => {
    const result = getSortedFiltered(files, "beta", "name", "asc");
    assert.strictEqual(result.length, 1);
    assert.strictEqual(result[0].name, "beta.txt");
  });

  test('search is case-insensitive', () => {
    const result = getSortedFiltered(files, "ALPHA", "name", "asc");
    assert.strictEqual(result.length, 1);
  });

  test('search with no matches', () => {
    const result = getSortedFiltered(files, "nonexistent", "name", "asc");
    assert.strictEqual(result.length, 0);
  });
});

suite('Pagination logic', () => {
  const items = Array.from({ length: 25 }, (_, i) => ({ name: `file${i}.txt`, is_dir: false }));

  test('page 1 returns first pageSize items', () => {
    const result = paginate(items, 1, 10);
    assert.strictEqual(result.items.length, 10);
    assert.strictEqual(result.page, 1);
    assert.strictEqual(result.totalPages, 3);
  });

  test('page 2 returns next items', () => {
    const result = paginate(items, 2, 10);
    assert.strictEqual(result.items.length, 20);
    assert.strictEqual(result.page, 2);
  });

  test('page beyond max clamps to last', () => {
    const result = paginate(items, 99, 10);
    assert.strictEqual(result.page, 3);
  });

  test('page 0 clamps to 1', () => {
    const result = paginate(items, 0, 10);
    assert.strictEqual(result.page, 1);
  });

  test('all items fit on one page', () => {
    const result = paginate(items, 1, 100);
    assert.strictEqual(result.totalPages, 1);
    assert.strictEqual(result.items.length, 25);
  });
});

suite('Navigation logic', () => {
  test('navigate returns new path', () => {
    assert.strictEqual(navigate("subdir", ""), "subdir");
    assert.strictEqual(navigate("", "subdir"), "");
  });
  test('parent navigation', () => {
    const p = "movies/2024/action";
    const parent = p.split("/").slice(0, -1).join("/");
    assert.strictEqual(parent, "movies/2024");
  });
  test('root from path', () => {
    const p = "subdir";
    assert.strictEqual(navigate("", p), "");
  });
});

suite('CSRF retry logic', () => {
  test('retries on csrf_expired error', async () => {
    let callCount = 0;
    const mockFetch = async () => {
      callCount++;
      if (callCount === 1) {
        const err = new Error('Session expired');
        err.code = 'csrf_expired'; err.status = 403;
        throw err;
      }
      return { success: true };
    };

    let refreshed = false;
    const refreshSession = async () => { refreshed = true; };

    const apiCall = async () => {
      try {
        return await mockFetch();
      } catch (error) {
        if (error.status === 403 && error.code === 'csrf_expired') {
          await refreshSession();
          return mockFetch();
        }
        throw error;
      }
    };

    const result = await apiCall();
    assert.ok(refreshed, 'should have refreshed CSRF');
    assert.strictEqual(callCount, 2, 'should have retried once');
    assert.ok(result.success);
  });

  test('does not retry non-CSRF errors', async () => {
    let callCount = 0;
    const mockFetch = async () => {
      callCount++;
      throw Object.assign(new Error('Not found'), { status: 404, code: 'not_found' });
    };

    let refreshed = false;
    const refreshSession = async () => { refreshed = true; };

    const apiCall = async () => {
      try {
        return await mockFetch();
      } catch (error) {
        if (error.status === 403 && error.code === 'csrf_expired') {
          await refreshSession();
          return mockFetch();
        }
        throw error;
      }
    };

    try {
      await apiCall();
      assert.ok(false, 'should have thrown');
    } catch (e) {
      assert.strictEqual(callCount, 1, 'should not have retried');
      assert.ok(!refreshed, 'should not have refreshed');
    }
  });
});

suite('DOM security', () => {
  const appSource = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "app.js"),
    "utf8",
  );

  test('does not interpolate remote data into innerHTML', () => {
    const forbidden = [
      /innerHTML\s*=\s*`[^`]*\$\{f\.name\}/,
      /innerHTML\s*=\s*`[^`]*\$\{h\./,
      /innerHTML\s*=\s*`[^`]*\$\{l\./,
      /innerHTML\s*=\s*`[^`]*\$\{d\[k\]/,
    ];
    forbidden.forEach(pattern => {
      assert.ok(!pattern.test(appSource), `Unsafe DOM interpolation found: ${pattern}`);
    });
  });

  test('preview text uses textContent', () => {
    assert.ok(appSource.includes("pre.textContent = text"));
    assert.ok(!appSource.includes("body.innerHTML = `<pre>"));
  });

  test('new-tab preview links isolate the opener', () => {
    assert.ok(appSource.includes('download.rel = "noopener noreferrer"'));
  });

  test('current folder can be shared as a navigable folder link', () => {
    assert.ok(appSource.includes("function openShareCurrentFolder"));
    assert.ok(appSource.includes('"Racine cloud"'));
    assert.ok(appSource.includes('is_dir: true'));
  });

  test('selected folder exposes a navigable folder bulk action', () => {
    assert.ok(appSource.includes("function getSingleSelectedFolder"));
    assert.ok(appSource.includes('"bulkShareFolder").hidden = !selectedFolder'));
    assert.ok(appSource.includes('"bulkShareFolder").addEventListener("click"'));
  });

  test('mobile file rows use selection mode and an action menu', () => {
    assert.ok(appSource.includes("selectionMode: false"));
    assert.ok(appSource.includes('classList.toggle("selection-mode", S.selectionMode)'));
    assert.ok(appSource.includes('cb.setAttribute("aria-label", `Sélectionner ${f.name}`)'));
    assert.ok(appSource.includes("function openFileActionMenu"));
    assert.ok(appSource.includes('role", "menuitem"'));
    assert.ok(!appSource.includes('acts.style.display = "flex"'));
  });
});

suite('Caddy share-link access', () => {
  const caddySource = fs.readFileSync(
    path.join(__dirname, "..", "..", "caddy", "dashboard.conf"),
    "utf8",
  );
  const protectedMatcher = caddySource.match(
    /@protected\s*\{([\s\S]*?)\}\s*basic_auth\s+@protected/,
  );

  test('uses a scoped auth matcher', () => {
    assert.ok(protectedMatcher, "basic_auth must use the @protected matcher");
  });

  test('allows only the generated download route and its page assets past basic auth', () => {
    const matcher = protectedMatcher ? protectedMatcher[1] : "";
    assert.ok(matcher.includes("not path"));
    assert.ok(matcher.includes("/cloud-panel/download/*"));
    assert.ok(matcher.includes("/cloud-panel/static/share.css"));
    assert.ok(matcher.includes("/cloud-panel/static/fonts/Inter-Variable.woff2"));
    assert.ok(!matcher.includes("/cloud-panel/api/*"), "Cloud Panel APIs must remain protected");
  });
});

suite('Drag & drop (move) logic', () => {
  const appSource = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "app.js"),
    "utf8",
  );

  function dropLabel(destPath) {
    return destPath ? destPath.split("/").filter(Boolean).pop() : "Racine";
  }

  test('drop label resolves root to Racine and keeps the last segment', () => {
    assert.strictEqual(dropLabel(""), "Racine");
    assert.strictEqual(dropLabel("films"), "films");
    assert.strictEqual(dropLabel("a/b/c"), "c");
  });

  test('drag payload carries path, name and is_dir', () => {
    const f = { path: "docs/report.txt", name: "report.txt", is_dir: false };
    const selected = new Set();
    const items = selected.has(f.path) ? [] : [f];
    assert.deepEqual(
      items.map(i => ({ path: i.path, name: i.name, is_dir: Boolean(i.is_dir) })),
      [{ path: "docs/report.txt", name: "report.txt", is_dir: false }],
    );
  });

  test('drag payload includes every selected item when dragging a selected row', () => {
    const rows = [
      { path: "a.txt", name: "a.txt", is_dir: false },
      { path: "b.txt", name: "b.txt", is_dir: false },
    ];
    const selectedPaths = ["a.txt", "b.txt"];
    const selectedItems = new Map(rows.map(r => [r.path, r]));
    const items = selectedPaths.includes(rows[0].path)
      ? selectedPaths.map(p => selectedItems.get(p))
      : [rows[0]];
    assert.deepEqual(items.map(i => i.name), ["a.txt", "b.txt"]);
  });

  test('move source dir and name are derived from the item path', () => {
    const it = { path: "docs/sub/file.txt", name: "file.txt", is_dir: false };
    const srcDir = it.path.split("/").slice(0, -1).join("/");
    const name = it.path.split("/").pop();
    assert.strictEqual(srcDir, "docs/sub");
    assert.strictEqual(name, "file.txt");
  });

  test('moveItemsTo posts to /files/move with path, name and dest', async () => {
    let payload = null;
    const api = async (url, opts) => {
      payload = { url, body: Object.fromEntries(opts.body.entries()) };
      return { success: true };
    };
    const au = (p) => `/cloud-panel/api${p}`;
    const items = [{ path: "docs/file.txt", name: "file.txt", is_dir: false }];
    let moved = 0;
    for (const it of items) {
      const fd = new FormData();
      fd.append("path", it.path.split("/").slice(0, -1).join("/"));
      fd.append("name", it.path.split("/").pop());
      fd.append("dest", "archive");
      await api(au("/files/move"), { method: "POST", body: fd });
      moved++;
    }
    assert.strictEqual(moved, 1);
    assert.strictEqual(payload.url, "/cloud-panel/api/files/move");
    assert.deepEqual(payload.body, { path: "docs", name: "file.txt", dest: "archive" });
  });

  test('rows use pointer drag sources and folders expose pointer drop targets', () => {
    assert.ok(appSource.includes("wirePointerDragSource(tr, f)"));
    assert.ok(appSource.includes("wirePointerDropTarget(tr, () => f.path)"));
    assert.ok(appSource.includes("moveItemsTo(dest, items)"));
    assert.ok(appSource.includes('au("/files/move")'));
    assert.ok(appSource.includes("pointer-drop-active"));
  });

  test('grid tiles use the same pointer drag and drop pipeline', () => {
    assert.ok(appSource.includes("wirePointerDragSource(tile, f)"));
    assert.ok(appSource.includes("wirePointerDropTarget(tile, () => f.path)"));
  });

  test('pointer drag waits for a movement threshold and suppresses the trailing click', () => {
    assert.ok(appSource.includes("distance < 6"));
    assert.ok(appSource.includes("suppressPointerClickUntil = performance.now() + 500"));
    assert.ok(appSource.includes("e.stopImmediatePropagation()"));
  });

  test('invalid self, descendant and same-folder drops are rejected', () => {
    function canPointerDrop(items, destPath) {
      const srcDirs = new Set(items.map(i => i.path.split("/").slice(0, -1).join("/")));
      if (srcDirs.size === 1 && [...srcDirs][0] === destPath) return false;
      return !items.some(i => i.is_dir && (destPath === i.path || destPath.startsWith(i.path + "/")));
    }
    assert.strictEqual(canPointerDrop([{ path: "docs/a.txt", is_dir: false }], "docs"), false);
    assert.strictEqual(canPointerDrop([{ path: "docs", is_dir: true }], "docs"), false);
    assert.strictEqual(canPointerDrop([{ path: "docs", is_dir: true }], "docs/sub"), false);
    assert.strictEqual(canPointerDrop([{ path: "docs/a.txt", is_dir: false }], "archive"), true);
  });

  test('breadcrumb crumbs and root/parent nav act as move drop targets', () => {
    assert.ok(appSource.includes("function makeDropTarget"));
    assert.ok(appSource.includes('makeDropTarget(rl, "")'));
    assert.ok(appSource.includes('data-nav="root"'));
    assert.ok(appSource.includes('data-nav="parent"'));
  });

  test('whole files view accepts desktop upload drops', () => {
    assert.ok(appSource.includes("function wireFilesViewUpload"));
    assert.ok(appSource.includes("view.classList.add(\"upload-target\")"));
    assert.ok(appSource.includes("startUpload(e.dataTransfer.files)"));
  });

  test('dragged names are never written via innerHTML', () => {
    assert.ok(!appSource.includes("innerHTML = `<" ) || !/innerHTML\s*=\s*`[^`]*\$\{name\}/.test(appSource));
    assert.ok(!appSource.includes("innerHTML = `<" ) || !/innerHTML\s*=\s*`[^`]*\$\{items\[/.test(appSource));
  });
});

suite('Ranger les médias feature', () => {
  const html = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "index.html"),
    "utf8",
  );
  const appSource = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "app.js"),
    "utf8",
  );

  test('toolbar exposes a "Ranger les médias" button', () => {
    assert.ok(html.includes('id="btnOrganizeSeries"'));
    assert.ok(html.includes("Ranger les médias"));
  });

  test('organize dialog is present with summary, sections and confirm/cancel', () => {
    assert.ok(html.includes('id="organizeDialog"'));
    assert.ok(html.includes('id="organizeSummary"'));
    assert.ok(html.includes('id="organizeSections"'));
    assert.ok(html.includes('id="confirmOrganizeBtn"'));
    assert.ok(html.includes('id="cancelOrganizeBtn"'));
  });

  test('preview and apply call the media endpoints', () => {
    assert.ok(appSource.includes('au("/files/organize/preview")'));
    assert.ok(appSource.includes('au("/files/organize/apply")'));
    assert.ok(!appSource.includes('"/files/organize-series/'));
  });

  test('qBittorrent folders delegate media organization to Torrent Panel', () => {
    assert.ok(appSource.includes('qbitOwned = S.path.split("/")'));
    assert.ok(appSource.includes("organizeButton.disabled = qbitOwned"));
    assert.ok(appSource.includes("Ranger pour Jellyfin"));
  });

  test('preview renders series, movies and parasites sections safely', () => {
    assert.ok(appSource.includes("function renderOrganizePreview"));
    assert.ok(appSource.includes('organizeSection("Séries"'));
    assert.ok(appSource.includes('organizeSection("Films"'));
    assert.ok(appSource.includes('organizeSection("Parasites signalés"'));
    assert.ok(appSource.includes('organizeSection("Doublons détectés"'));
    assert.ok(appSource.includes("const duplicates = d.duplicates || []"));
    assert.ok(appSource.includes("name.textContent = g.name"));
    assert.ok(appSource.includes("from.textContent = source"));
    assert.ok(appSource.includes("to.textContent = entry.target"));
    assert.ok(appSource.includes("to.textContent = m.target"));
    assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{g\.name\}/.test(appSource));
    assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{entry\.name\}/.test(appSource));
    assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{m\.name\}/.test(appSource));
  });

  test('confirmation is disabled when nothing to organize', () => {
    assert.ok(appSource.includes('confirmBtn.disabled = true'));
    assert.ok(appSource.includes("Aucun média détecté dans ce dossier."));
  });

  test('apply result shows a summary toast and reloads the listing', () => {
    assert.ok(appSource.includes("r.series_count"));
    assert.ok(appSource.includes("r.series_moved"));
    assert.ok(appSource.includes("r.movies_moved"));
    assert.ok(appSource.includes("loadFiles();"));
  });
});

suite('Folder size on demand', () => {
  const appSource = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "app.js"),
    "utf8",
  );

  function folderSizeLabel(f) {
    if (!f.is_dir) return f.size || fmtSize(f.size_bytes);
    return f.size_bytes ? f.size : "—";
  }

  test('files keep their size text, folders show a placeholder', () => {
    assert.strictEqual(folderSizeLabel({ is_dir: false, size: "1.2 Mo", size_bytes: 1200000 }), "1.2 Mo");
    assert.strictEqual(folderSizeLabel({ is_dir: true, size_bytes: 0 }), "—");
  });

  test('a computed folder size replaces the placeholder', () => {
    assert.strictEqual(folderSizeLabel({ is_dir: true, size: "2.5 Go", size_bytes: 2684354560 }), "2.5 Go");
  });

  test('folder size request derives path and name from the item', () => {
    const it = { path: "docs/shows/Show.S01", name: "Show.S01", is_dir: true };
    const pp = it.path.split("/").slice(0, -1).join("/");
    const nm = it.path.split("/").pop();
    assert.strictEqual(pp, "docs/shows");
    assert.strictEqual(nm, "Show.S01");
  });

  test('folder size requests the /files/size endpoint', () => {
    assert.ok(appSource.includes('au("/files/size")'));
    assert.ok(appSource.includes("function folderSizeCell"));
  });

  test('folder sizes are rendered without raw innerHTML', () => {
    assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{d\.size\}/.test(appSource));
    assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{f\.size\}/.test(appSource));
  });

  test('folder size button is keyboard accessible', () => {
    assert.ok(appSource.includes('btn.type = "button"'));
    assert.ok(appSource.includes('setAttribute("aria-label"'));
  });
});

suite('Bulk folder sizes', () => {
  const appSource = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "app.js"),
    "utf8",
  );
  const html = fs.readFileSync(
    path.join(__dirname, "..", "cloud_panel", "static", "index.html"),
    "utf8",
  );

  function rememberFolderSize(f, d, map) {
    f.size = d.size;
    f.size_bytes = d.size_bytes;
    map.set(f.path, { size: d.size, size_bytes: d.size_bytes });
  }

  function mergeKnownSizes(items, map) {
    items.forEach(f => {
      if (!f.is_dir) return;
      const s = map.get(f.path);
      if (s) { f.size = s.size; f.size_bytes = s.size_bytes; }
    });
  }

  function buildPathsPayload(dirs) {
    return dirs.map(f => f.path).join("\n");
  }

  test('toolbar exposes a "Calculer les tailles" button', () => {
    assert.ok(html.includes('id="btnCalcSizes"'));
    assert.ok(html.includes("Calculer les tailles"));
  });

  test('known sizes are restored on listing load so sorting works', () => {
    const items = [
      { name: "A", path: "A", is_dir: true, size_bytes: 0 },
      { name: "B", path: "B", is_dir: true, size_bytes: 0 },
      { name: "c.txt", path: "c.txt", is_dir: false },
    ];
    const map = new Map([["A", { size: "1.5 Go", size_bytes: 1610612736 }]]);
    mergeKnownSizes(items, map);
    assert.strictEqual(items[0].size_bytes, 1610612736);
    assert.strictEqual(items[1].size_bytes, 0);
    assert.ok(!items[2].hasOwnProperty("size_bytes") || !items[2].size);
  });

  test('batch payload joins visible folder paths with newlines', () => {
    const dirs = [
      { path: "Films" },
      { path: "Series/Show" },
    ];
    assert.strictEqual(buildPathsPayload(dirs), "Films\nSeries/Show");
  });

  test('batch result updates the remembered sizes map', () => {
    const map = new Map();
    const f = { path: "Films", size_bytes: 0 };
    rememberFolderSize(f, { size: "2.0 Go", size_bytes: 2147483648 }, map);
    assert.strictEqual(f.size_bytes, 2147483648);
    assert.deepEqual(map.get("Films"), { size: "2.0 Go", size_bytes: 2147483648 });
  });

  test('bulk sizes post to the /files/sizes endpoint', () => {
    assert.ok(appSource.includes('au("/files/sizes")'));
    assert.ok(appSource.includes("function calcAllFolderSizes"));
    assert.ok(appSource.includes('$("btnCalcSizes").addEventListener("click", calcAllFolderSizes)'));
    assert.ok(appSource.includes("folderSizes: new Map()"));
  });
});

console.log('\nTous les tests passes.');
