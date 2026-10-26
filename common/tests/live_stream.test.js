const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "js", "live.js"), "utf8");
const tests = [];

function test(name, run) {
  tests.push({ name, run });
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function createDocument() {
  const listeners = new Map();
  return {
    hidden: false,
    addEventListener(name, handler) {
      listeners.set(name, handler);
    },
    removeEventListener(name, handler) {
      if (listeners.get(name) === handler) listeners.delete(name);
    },
    dispatch(name) {
      const handler = listeners.get(name);
      if (handler) handler();
    },
    listenerCount() {
      return listeners.size;
    },
  };
}

function loadLive({ document = createDocument(), EventSource } = {}) {
  const window = {
    document,
    EventSource,
    setTimeout,
    clearTimeout,
  };
  const sandbox = {
    window,
    document,
    AbortController,
    Date,
    JSON,
    Object,
    Array,
    Promise,
    setTimeout,
    clearTimeout,
    console,
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { window, api: window.DashboardLive, document };
}

async function waitUntil(predicate, timeoutMs = 1000) {
  const started = Date.now();
  while (!predicate()) {
    if (Date.now() - started > timeoutMs) {
      throw new Error("condition was not reached in time");
    }
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

test("missing EventSource uses polling and emits a full snapshot", async () => {
  const document = createDocument();
  const { api } = loadLive({ document });
  const received = deferred();
  let subscription;
  subscription = api.createLiveSubscription({
    document,
    EventSource: undefined,
    streamUrl: "/api/events",
    pollIntervalMs: 100,
    reconnectDelayMs: 10000,
    poll: async () => ({
      sections: {
        files: { items: ["a"], total: 1 },
      },
      jobs: [{ id: "1", status: "running" }],
    }),
    handlers: {
      snapshot(frame) {
        assert.equal(frame.topic, "files");
        assert.deepEqual(frame.data, { items: ["a"], total: 1 });
        received.resolve();
        subscription.stop();
      },
      job(frame) {
        assert.equal(frame.job.id, "1");
      },
    },
  });

  subscription.start();
  await received.promise;
  assert.equal(subscription.status(), "stopped");
  assert.equal(document.listenerCount(), 0);
});

test("SSE updates are parsed, invalid data yields only a public error", async () => {
  const document = createDocument();
  class FakeEventSource {
    static instances = [];
    constructor(url) {
      this.url = url;
      this.listeners = new Map();
      this.closed = false;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener(name, handler) {
      if (this.listeners.get(name) === handler) this.listeners.delete(name);
    }
    emit(name, data) {
      const handler = this.listeners.get(name);
      if (handler) handler({ data });
    }
    close() {
      this.closed = true;
    }
  }
  const snapshots = [];
  const errors = [];
  const { api } = loadLive({ document, EventSource: FakeEventSource });
  const subscription = api.createLiveSubscription({
    document,
    EventSource: FakeEventSource,
    streamUrl: "/api/events",
    handlers: {
      snapshot(frame) {
        snapshots.push(frame);
      },
      error(error) {
        errors.push(error);
      },
    },
  });

  subscription.start();
  const eventSource = FakeEventSource.instances[0];
  eventSource.onopen();
  eventSource.emit(
    "snapshot",
    JSON.stringify({
      type: "snapshot",
      topic: "torrents",
      data: { items: 2 },
      sequence: 1,
    }),
  );
  eventSource.emit("section", "{not-json");
  assert.equal(snapshots.length, 1);
  assert.equal(snapshots[0].data.items, 2);
  assert.equal(errors.length, 1);
  assert.equal(errors[0].code, "invalid_stream_frame");
  assert.doesNotMatch(JSON.stringify(errors), /not-json|SyntaxError/);
  subscription.stop();
});

test("heartbeat and lightweight freshness frames reach their handlers", async () => {
  const document = createDocument();
  class FakeEventSource {
    static instances = [];
    constructor() {
      this.listeners = new Map();
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener() {}
    close() {}
  }
  const heartbeats = [];
  const freshness = [];
  const { api } = loadLive({ document, EventSource: FakeEventSource });
  const subscription = api.createLiveSubscription({
    document,
    EventSource: FakeEventSource,
    streamUrl: "/api/events",
    handlers: {
      heartbeat(frame) {
        heartbeats.push(frame);
      },
      freshness(frame) {
        freshness.push(frame);
      },
    },
  });
  subscription.start();
  const source = FakeEventSource.instances[0];
  source.listeners.get("heartbeat")({
    data: JSON.stringify({ type: "heartbeat", heartbeatAt: "now", sequence: 1 }),
  });
  source.listeners.get("freshness")({
    data: JSON.stringify({
      type: "freshness",
      topic: "files",
      collectedAt: "now",
      unchanged: true,
      stale: false,
      sequence: 2,
    }),
  });
  assert.equal(heartbeats.length, 1);
  assert.equal(freshness[0].topic, "files");
  assert.equal(freshness[0].unchanged, true);
  subscription.stop();
});

test("hidden documents pause networking and visibility reconnects with full SSE", async () => {
  const document = createDocument();
  let pollCalls = 0;
  class FakeEventSource {
    static instances = [];
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener() {}
    close() {
      this.closed = true;
    }
  }
  const snapshots = [];
  const statuses = [];
  const { api } = loadLive({ document, EventSource: FakeEventSource });
  const subscription = api.createLiveSubscription({
    document,
    EventSource: FakeEventSource,
    streamUrl: "/api/events",
    poll: async () => {
      pollCalls += 1;
      return { sections: {}, jobs: [] };
    },
    handlers: {
      snapshot(frame) {
        snapshots.push(frame);
      },
      status(value) {
        statuses.push(value.mode);
      },
    },
  });

  subscription.start();
  assert.equal(FakeEventSource.instances.length, 1);
  document.hidden = true;
  document.dispatch("visibilitychange");
  assert.equal(FakeEventSource.instances[0].closed, true);
  assert.equal(pollCalls, 0);
  assert.equal(statuses.at(-1), "paused");

  document.hidden = false;
  document.dispatch("visibilitychange");
  assert.equal(FakeEventSource.instances.length, 2);
  const reconnected = FakeEventSource.instances[1];
  reconnected.onopen();
  reconnected.listeners.get("snapshot")({
    data: JSON.stringify({
      type: "snapshot",
      topic: "files",
      data: { items: ["fresh"], total: 1 },
      sequence: 9,
    }),
  });
  assert.equal(snapshots.at(-1).data.total, 1);
  subscription.stop();
});

test("an SSE failure starts polling without losing automatic reconnect", async () => {
  const document = createDocument();
  const polled = deferred();
  class FakeEventSource {
    static instances = [];
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener() {}
    close() {
      this.closed = true;
    }
  }
  const { api } = loadLive({ document, EventSource: FakeEventSource });
  const statuses = [];
  const subscription = api.createLiveSubscription({
    document,
    EventSource: FakeEventSource,
    streamUrl: "/api/events",
    pollIntervalMs: 100,
    reconnectDelayMs: 10000,
    poll: async () => {
      polled.resolve();
      return { sections: { health: { ok: true } }, jobs: [] };
    },
    handlers: {
      status(value) {
        statuses.push(value.mode);
      },
      snapshot() {
        subscription.stop();
      },
    },
  });

  subscription.start();
  FakeEventSource.instances[0].onerror();
  await polled.promise;
  await waitUntil(() => subscription.status() === "stopped");
  assert.ok(statuses.includes("polling"));
  assert.equal(FakeEventSource.instances[0].closed, true);
});

test("a stopped or hidden poll cannot publish a late response or start twice", async () => {
  const document = createDocument();
  const response = deferred();
  let calls = 0;
  let snapshots = 0;
  const { api } = loadLive({ document });
  const subscription = api.createLiveSubscription({
    document,
    EventSource: undefined,
    streamUrl: "/api/events",
    pollIntervalMs: 100,
    reconnectDelayMs: 10000,
    poll: async () => {
      calls += 1;
      return response.promise;
    },
    handlers: {
      snapshot() {
        snapshots += 1;
      },
    },
  });

  subscription.start();
  await waitUntil(() => calls === 1);
  document.hidden = true;
  document.dispatch("visibilitychange");
  response.resolve({ sections: { files: { late: true } }, jobs: [] });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(snapshots, 0, "late poll response must not update a hidden page");
  assert.equal(calls, 1, "hidden fallback polling must stop");
  subscription.stop();
});

test("repeated SSE failures keep one fallback poll loop active", async () => {
  const document = createDocument();
  let pollCalls = 0;
  class FakeEventSource {
    static instances = [];
    constructor() {
      this.listeners = new Map();
      this.closed = false;
      FakeEventSource.instances.push(this);
    }
    addEventListener(name, handler) {
      this.listeners.set(name, handler);
    }
    removeEventListener() {}
    close() {
      this.closed = true;
    }
  }
  const held = deferred();
  const { api } = loadLive({ document, EventSource: FakeEventSource });
  const subscription = api.createLiveSubscription({
    document,
    EventSource: FakeEventSource,
    streamUrl: "/api/events",
    pollIntervalMs: 10000,
    reconnectDelayMs: 10000,
    poll: async () => {
      pollCalls += 1;
      return held.promise;
    },
  });

  subscription.start();
  const failedSource = FakeEventSource.instances[0];
  const onError = failedSource.onerror;
  onError();
  onError();
  await waitUntil(() => pollCalls === 1);
  assert.equal(pollCalls, 1);
  subscription.stop();
  held.resolve({ sections: {}, jobs: [] });
});

test("a real directory section envelope is accepted only for its current path", () => {
  const { api } = loadLive();
  const summary = {
    path: "Series/Show",
    current_path: "Series/Show",
    fingerprint: "b".repeat(64),
    total: 257,
    source_age_ms: 420,
    source_scanned_at: "2026-10-26T12:00:00.000Z",
  };
  const unchanged = api.evaluateDirectoryPayload(summary, "Series/Show", summary.fingerprint, false);
  const changed = api.evaluateDirectoryPayload(summary, "/Series/Show/", "a".repeat(64), false);
  const wrongPath = api.evaluateDirectoryPayload(summary, "Series/Other", null, true);

  assert.equal(unchanged.accepted, true);
  assert.equal(unchanged.changed, false);
  assert.equal(unchanged.sourceAgeMs, 420);
  assert.equal(changed.accepted, true);
  assert.equal(changed.changed, true);
  assert.equal(wrongPath.accepted, false);
});

(async () => {
  for (const current of tests) {
    await current.run();
    console.log("ok - " + current.name);
  }
  console.log(tests.length + " live stream tests passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
