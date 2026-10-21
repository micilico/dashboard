const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "js", "api.js"), "utf8");
const tests = [];

function test(name, run) {
  tests.push({ name, run });
}

function createDocument() {
  let banner = null;
  return {
    body: {
      prepend(node) { banner = node; },
    },
    createElement(tagName) {
      return {
        tagName: tagName.toUpperCase(),
        id: "",
        className: "",
        textContent: "",
        setAttribute(name, value) { this[name] = value; },
        remove() { banner = null; },
      };
    },
    getElementById(id) { return id === "reconnect-banner" ? banner : null; },
  };
}

function loadApi(fetchImpl, { setTimeoutImpl = setTimeout, clearTimeoutImpl = clearTimeout } = {}) {
  const sandboxWindow = {
    setTimeout: setTimeoutImpl,
    clearTimeout: clearTimeoutImpl,
  };
  const sandbox = {
    window: sandboxWindow,
    document: createDocument(),
    fetch: fetchImpl,
    Headers,
    FormData,
    URLSearchParams,
    AbortController,
    DOMException,
    setTimeout: setTimeoutImpl,
    clearTimeout: clearTimeoutImpl,
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${source}\n;globalThis.__api = { fetchWithRetry, createApiClient };`, sandbox);
  return sandbox.__api;
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return payload; },
  };
}

test("caller abort propagates once and skips retry backoff", async () => {
  const caller = new AbortController();
  let calls = 0;
  let fetchSignal;
  const { fetchWithRetry } = loadApi((_url, options) => {
    calls += 1;
    fetchSignal = options.signal;
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
    });
  });

  const pending = fetchWithRetry("/api/torrents", { signal: caller.signal, timeout: 1000 }, 3);
  caller.abort();
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(calls, 1);
  assert.notEqual(fetchSignal, caller.signal);
  assert.equal(fetchSignal.aborted, true);
});

test("an already-aborted signal prevents the request", async () => {
  const caller = new AbortController();
  caller.abort();
  let calls = 0;
  const { fetchWithRetry } = loadApi(async () => { calls += 1; });

  await assert.rejects(fetchWithRetry("/api/torrents", { signal: caller.signal }), (error) => error.name === "AbortError");
  assert.equal(calls, 0);
});

test("the API timeout also covers response-body parsing", async () => {
  let fetchSignal;
  const { createApiClient } = loadApi(async (_url, options) => {
    fetchSignal = options.signal;
    return {
      ok: true,
      status: 200,
      json() {
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
        });
      },
    };
  });

  await assert.rejects(
    createApiClient().request("/api/torrents", { timeout: 5, retry: false }),
    (error) => error.name === "TimeoutError"
      && error.code === "request_timeout"
      && error.recovery === "Réessayer",
  );
  assert.ok(fetchSignal.aborted);
});

test("network retries remain enabled and successful", async () => {
  let calls = 0;
  const fastRetryTimers = (callback, delayMs) => setTimeout(callback, delayMs === 1000 ? 0 : delayMs);
  const { fetchWithRetry } = loadApi(async () => {
    calls += 1;
    if (calls === 1) throw new Error("temporary network failure");
    return jsonResponse(200, { ok: true });
  }, { setTimeoutImpl: fastRetryTimers });

  const response = await fetchWithRetry("/api/torrents", { timeout: 1000 }, 1);
  assert.equal(calls, 2);
  assert.equal(response.status, 200);
});

test("mutations are not replayed after an ambiguous network failure", async () => {
  let calls = 0;
  const { createApiClient } = loadApi(async () => {
    calls += 1;
    throw new TypeError("network disconnected");
  });

  await assert.rejects(
    createApiClient().request("/api/files/delete", { method: "POST", body: "{}" }),
    /network disconnected/,
  );
  assert.equal(calls, 1);
});

test("a mutation can explicitly opt into retries", async () => {
  let calls = 0;
  const fastRetryTimers = (callback, delayMs) => setTimeout(callback, delayMs === 1000 ? 0 : delayMs);
  const { createApiClient } = loadApi(async () => {
    calls += 1;
    if (calls === 1) throw new TypeError("temporary network failure");
    return jsonResponse(200, { ok: true });
  }, { setTimeoutImpl: fastRetryTimers });

  const payload = await createApiClient().request("/api/items", {
    method: "POST",
    body: "{}",
    retry: true,
  });
  assert.equal(payload.ok, true);
  assert.equal(calls, 2);
});

test("server errors are retried for reads and eventually surface", async () => {
  let calls = 0;
  const fastRetryTimers = (callback, delayMs) => setTimeout(callback, delayMs === 1000 ? 0 : delayMs);
  const { createApiClient } = loadApi(async () => {
    calls += 1;
    return calls === 1
      ? jsonResponse(503, { detail: { code: "busy", message: "Service occupé", recovery: "Réessayer" } })
      : jsonResponse(200, { ok: true });
  }, { setTimeoutImpl: fastRetryTimers });

  const payload = await createApiClient().request("/api/torrents");
  assert.equal(payload.ok, true);
  assert.equal(calls, 2);
});

test("client errors are returned without retrying", async () => {
  let calls = 0;
  const { createApiClient } = loadApi(async () => {
    calls += 1;
    return jsonResponse(400, { detail: { code: "invalid", message: "Requête invalide" } });
  });

  await assert.rejects(
    createApiClient().request("/api/torrents"),
    (error) => error.code === "invalid" && error.status === 400,
  );
  assert.equal(calls, 1);
});

test("caller abort interrupts the retry backoff before another fetch", async () => {
  const caller = new AbortController();
  let calls = 0;
  const { fetchWithRetry } = loadApi(async () => {
    calls += 1;
    throw new Error("temporary network failure");
  });

  const pending = fetchWithRetry("/api/torrents", { signal: caller.signal, timeout: 1000 }, 3);
  setTimeout(() => caller.abort(), 0);
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(calls, 1);
});

test("successful requests remove their caller abort listener", async () => {
  const caller = new AbortController();
  const addEventListener = caller.signal.addEventListener.bind(caller.signal);
  const removeEventListener = caller.signal.removeEventListener.bind(caller.signal);
  let additions = 0;
  let removals = 0;
  caller.signal.addEventListener = (...args) => { additions += 1; return addEventListener(...args); };
  caller.signal.removeEventListener = (...args) => { removals += 1; return removeEventListener(...args); };
  const { fetchWithRetry } = loadApi(async () => jsonResponse(200, { ok: true }));

  await fetchWithRetry("/api/torrents", { signal: caller.signal, timeout: 1000 }, 0);
  assert.equal(additions, 1);
  assert.equal(removals, 1);
});

test("request preserves headers, credentials and payload contract", async () => {
  const calls = [];
  const { createApiClient } = loadApi(async (url, options) => {
    calls.push({ url, options });
    return jsonResponse(200, { path: url });
  });
  const client = createApiClient({
    csrfHeader: "X-Test-CSRF",
    getCsrfToken: () => "csrf-token",
    sessionPath: "/api/session",
  });

  const payload = await client.request("/api/items", {
    method: "POST",
    body: JSON.stringify({ name: "Example" }),
    timeout: 12000,
    headers: { "X-Request": "test" },
  });

  assert.deepEqual(JSON.parse(JSON.stringify(payload)), { path: "/api/items" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.credentials, "same-origin");
  assert.equal(calls[0].options.timeout, undefined);
  assert.equal(calls[0].options.headers.get("Accept"), "application/json");
  assert.equal(calls[0].options.headers.get("Content-Type"), "application/json");
  assert.equal(calls[0].options.headers.get("X-Request"), "test");
  assert.equal(calls[0].options.headers.get("X-Test-CSRF"), "csrf-token");
});

test("CSRF refresh retries the original mutation with the renewed token", async () => {
  const calls = [];
  let token = "old-token";
  const { createApiClient } = loadApi(async (url, options) => {
    calls.push({ url, token: options.headers.get("X-Test-CSRF") });
    if (url === "/api/items") {
      return calls.length === 1
        ? jsonResponse(403, { detail: { code: "csrf_expired", message: "Session expirée", recovery: "Actualiser la page" } })
        : jsonResponse(200, { ok: true });
    }
    return jsonResponse(200, { csrfToken: "new-token" });
  });
  const client = createApiClient({
    csrfHeader: "X-Test-CSRF",
    getCsrfToken: () => token,
    setCsrfToken: (nextToken) => { token = nextToken; },
    sessionPath: "/api/session",
  });

  assert.deepEqual(Object.keys(await client.request("/api/items", { method: "POST", body: "{}" })), ["ok"]);
  assert.deepEqual(calls, [
    { url: "/api/items", token: "old-token" },
    { url: "/api/session", token: null },
    { url: "/api/items", token: "new-token" },
  ]);
});

test("caller abort cancels an in-flight CSRF session refresh", async () => {
  const caller = new AbortController();
  let calls = 0;
  const { createApiClient } = loadApi(async (url, options) => {
    calls += 1;
    if (url === "/api/items") {
      return jsonResponse(403, { detail: { code: "csrf_expired", message: "Session expirée" } });
    }
    return {
      ok: true,
      status: 200,
      json() {
        return new Promise((_resolve, reject) => {
          options.signal.addEventListener("abort", () => reject(options.signal.reason), { once: true });
        });
      },
    };
  });
  const client = createApiClient({
    csrfHeader: "X-Test-CSRF",
    getCsrfToken: () => "expired-token",
    sessionPath: "/api/session",
  });

  const pending = client.request("/api/items", {
    method: "POST",
    body: "{}",
    signal: caller.signal,
    timeout: 1000,
  });
  setTimeout(() => caller.abort(), 0);
  await assert.rejects(pending, (error) => error.name === "AbortError");
  assert.equal(calls, 2);
});

test("simultaneous identical GETs remain independent", async () => {
  let calls = 0;
  const { createApiClient } = loadApi(async () => {
    calls += 1;
    return jsonResponse(200, { request: calls });
  });
  const client = createApiClient();

  const [first, second] = await Promise.all([
    client.request("/api/files"),
    client.request("/api/files"),
  ]);
  assert.equal(calls, 2);
  assert.notEqual(first, second);
  first.request = 99;
  assert.equal(second.request, 2);
});

(async () => {
  for (const { name, run } of tests) {
    await run();
    console.log(`ok - ${name}`);
  }
  console.log(`api client contract ok (${tests.length} tests)`);
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
