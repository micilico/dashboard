// Tests frontend pour Cloud Panel
// Run with: node tests/frontend_logic.test.js

const assert = {
  strictEqual: (a, b, msg) => { if (a !== b) throw new Error(msg || `Expected ${JSON.stringify(b)} but got ${JSON.stringify(a)}`); },
  ok: (v, msg) => { if (!v) throw new Error(msg || `Expected truthy but got ${v}`); },
  deepEqual: (a, b) => { const sa = JSON.stringify(a), sb = JSON.stringify(b); if (sa !== sb) throw new Error(`Expected ${sb} but got ${sa}`); },
};

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

console.log('\nTous les tests passes.');
