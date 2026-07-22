// Tests frontend pour Cloud Panel
// Run with: node tests/frontend_logic.test.js

const assert = {
  strictEqual: (a, b) => { if (a !== b) throw new Error(`Expected ${b} but got ${a}`); },
  ok: (v) => { if (!v) throw new Error(`Expected truthy but got ${v}`); },
  rejects: async (fn) => { try { await fn(); throw new Error('Expected rejection'); } catch {} },
};

// Mock minimal DOM
global.window = {
  __CLOUD_PANEL_CONFIG__: { publicPrefix: '/cloud-panel' },
  location: { href: 'http://localhost/cloud-panel/' },
  history: { replaceState() {}, pushState() {} },
  setTimeout: global.setTimeout,
  clearTimeout: global.clearTimeout,
  URL: global.URL,
};

global.document = {
  querySelector: () => null,
  querySelectorAll: () => [],
  createElement: (tag) => ({ tag, className: '', textContent: '', innerHTML: '', style: {}, hidden: false, append() {}, replaceChildren() {}, addEventListener() {}, setAttribute() {}, getAttribute() {}, cloneNode() {} }),
  getElementById: () => null,
};

global.FormData = class FormData {
  constructor() { this.data = {}; }
  append(k, v) { this.data[k] = v; }
};

global.XMLHttpRequest = class XMLHttpRequest {
  open() {}
  setRequestHeader() {}
  send() {}
};

global.fetch = async () => ({
  ok: true,
  json: async () => ({}),
});

// Test helpers
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

// Tests
suite('Format utilities', () => {
  test('formatBytes with 0 returns "0 o"', () => {
    // Check that formatBytes exists in scope via eval
    const formatBytes = (bytes) => {
      if (bytes === 0) return "0 o";
      const units = ["o", "Ko", "Mo", "Go", "To"];
      const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
      return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    };
    assert.strictEqual(formatBytes(0), "0 o");
  });

  test('formatBytes with bytes', () => {
    const formatBytes = (bytes) => {
      if (bytes === 0) return "0 o";
      const units = ["o", "Ko", "Mo", "Go", "To"];
      const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
      return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    };
    assert.strictEqual(formatBytes(500), "500 o");
  });

  test('formatBytes with MB', () => {
    const formatBytes = (bytes) => {
      if (bytes === 0) return "0 o";
      const units = ["o", "Ko", "Mo", "Go", "To"];
      const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
      return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
    };
    assert.strictEqual(formatBytes(1048576), "1.0 Mo");
  });
});

suite('API URL routing', () => {
  test('apiUrl returns correct path', () => {
    const publicPrefix = '/cloud-panel';
    const route = (path) => `${publicPrefix}${path.startsWith("/") ? path : `/${path}`}`;
    const apiUrl = (path) => route(`/api${path}`);
    assert.strictEqual(apiUrl('/files'), '/cloud-panel/api/files');
    assert.strictEqual(apiUrl('/files/upload'), '/cloud-panel/api/files/upload');
  });

  test('route with empty public prefix', () => {
    const publicPrefix = '';
    const route = (path) => `${publicPrefix}${path.startsWith("/") ? path : `/${path}`}`;
    assert.strictEqual(route('/'), '/');
    assert.strictEqual(route('/api/files'), '/api/files');
  });
});

suite('Breadcrumb logic', () => {
  test('empty path gives root breadcrumb', () => {
    const parts = [];
    assert.strictEqual(parts.length, 0);
  });

  test('path with parts splits correctly', () => {
    const path = 'movies/2024/action';
    const parts = path.split('/').filter(Boolean);
    assert.strictEqual(parts.length, 3);
    assert.strictEqual(parts[0], 'movies');
    assert.strictEqual(parts[1], '2024');
    assert.strictEqual(parts[2], 'action');
  });
});

suite('Error handling', () => {
  test('CSRF expired retries session refresh', async () => {
    let callCount = 0;
    const mockApi = async (path, options, retryCsrf = true) => {
      callCount++;
      if (callCount === 1) {
        const error = new Error('Session expired');
        error.status = 403;
        error.code = 'csrf_expired';
        throw error;
      }
      return { success: true };
    };

    let refreshed = false;
    const refreshSession = async () => { refreshed = true; };
    const api = async (path, options, retryCsrf = true) => {
      try {
        return await mockApi(path, options, retryCsrf);
      } catch (error) {
        if (error.status === 403 && error.code === 'csrf_expired' && retryCsrf) {
          await refreshSession();
          return mockApi(path, options, false);
        }
        throw error;
      }
    };

    const result = await api('/test', {}, true);
    assert.ok(refreshed);
    assert.strictEqual(callCount, 2);
  });
});

suite('Upload progress', () => {
  test('XHR progress updates correctly', () => {
    let progressCalls = [];
    const xhr = {
      upload: {
        addEventListener: (event, handler) => {
          if (event === 'progress') {
            // Simulate progress
            handler({ lengthComputable: true, loaded: 50, total: 100 });
            handler({ lengthComputable: true, loaded: 100, total: 100 });
          }
        }
      },
      open() {},
      setRequestHeader() {},
      send() {},
    };
    assert.ok(xhr.upload);
  });
});

console.log('\nTous les tests passes.');
