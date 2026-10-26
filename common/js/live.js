(function installDashboardLive(root) {
  "use strict";

  var EVENT_NAMES = ["snapshot", "section", "freshness", "job", "heartbeat"];

  function publicError(code, message, recovery) {
    return { code: code, message: message, recovery: recovery || "Réessayer" };
  }

  function isRecord(value) {
    return value && typeof value === "object" && !Array.isArray(value);
  }

  function normalizeDirectoryPath(value) {
    return String(value || "").replace(/^\/+|\/+$/g, "");
  }

  function evaluateDirectoryPayload(payload, currentPath, previousFingerprint, changedHint) {
    if (!isRecord(payload) || payload.unavailable) return { accepted: false };
    var payloadPath = normalizeDirectoryPath(payload.path || payload.current_path);
    if (payloadPath !== normalizeDirectoryPath(currentPath)) return { accepted: false };
    var fingerprint = String(payload.fingerprint || "");
    var changed = Boolean(changedHint) || (
      previousFingerprint !== null && previousFingerprint !== undefined && previousFingerprint !== fingerprint
    );
    return {
      accepted: true,
      path: payloadPath,
      fingerprint: fingerprint,
      changed: changed,
      sourceAgeMs: Math.max(0, Number(payload.source_age_ms) || 0),
      sourceScannedAt: payload.source_scanned_at || null
    };
  }

  function createLiveSubscription(options) {
    options = options || {};
    var doc = options.document || root.document;
    var EventSourceCtor = Object.prototype.hasOwnProperty.call(options, "EventSource")
      ? options.EventSource
      : root.EventSource;
    var setTimeoutImpl = options.setTimeout || root.setTimeout.bind(root);
    var clearTimeoutImpl = options.clearTimeout || root.clearTimeout.bind(root);
    var pollIntervalMs = Math.max(100, Number(options.pollIntervalMs) || 500);
    var reconnectDelayMs = Math.max(500, Number(options.reconnectDelayMs) || 5000);
    var heartbeatTimeoutMs = Math.max(1000, Number(options.heartbeatTimeoutMs) || 35000);
    var handlers = options.handlers || {};
    var streamUrl = String(options.streamUrl || "");
    var poll = typeof options.poll === "function" ? options.poll : null;
    var started = false;
    var hidden = Boolean(doc && doc.hidden);
    var source = null;
    var reconnectTimer = null;
    var pollTimer = null;
    var heartbeatTimer = null;
    var pollController = null;
    var pollEpoch = 0;
    var polling = false;
    var mode = "idle";

    function call(name, value) {
      var handler = handlers[name];
      if (typeof handler !== "function") return;
      try {
        handler(value);
      } catch (error) {
        setTimeoutImpl(function () { throw error; }, 0);
      }
    }

    function status(nextMode) {
      mode = nextMode;
      call("status", {
        mode: nextMode,
        connected: nextMode === "sse",
        paused: nextMode === "paused"
      });
    }

    function clearTimer(name) {
      if (name === "reconnect" && reconnectTimer !== null) {
        clearTimeoutImpl(reconnectTimer);
        reconnectTimer = null;
      }
      if (name === "poll" && pollTimer !== null) {
        clearTimeoutImpl(pollTimer);
        pollTimer = null;
      }
      if (name === "heartbeat" && heartbeatTimer !== null) {
        clearTimeoutImpl(heartbeatTimer);
        heartbeatTimer = null;
      }
    }

    function closeSource() {
      if (source) {
        source.onopen = null;
        source.onerror = null;
        EVENT_NAMES.forEach(function (name) {
          if (typeof source.removeEventListener === "function") {
            source.removeEventListener(name, onEvent);
          }
        });
        try { source.close(); } catch (error) { /* already closed */ }
        source = null;
      }
      clearTimer("heartbeat");
    }

    function abortPolling() {
      pollEpoch += 1;
      clearTimer("poll");
      if (pollController) {
        try { pollController.abort(); } catch (error) { /* unsupported */ }
        pollController = null;
      }
      polling = false;
    }

    function stopNetwork() {
      closeSource();
      abortPolling();
      clearTimer("reconnect");
    }

    function validFrame(value) {
      return value && typeof value === "object" && !Array.isArray(value);
    }

    function parseFrame(raw) {
      try {
        var frame = JSON.parse(raw);
        return validFrame(frame) ? frame : null;
      } catch (error) {
        return null;
      }
    }

    function armHeartbeat() {
      clearTimer("heartbeat");
      heartbeatTimer = setTimeoutImpl(function () {
        if (!started || hidden || mode !== "sse") return;
        loseStream();
      }, heartbeatTimeoutMs);
    }

    function dispatch(frame) {
      if (!validFrame(frame)) {
        call("error", publicError(
          "invalid_stream_frame",
          "Le flux de données est invalide.",
          "Actualiser la page"
        ));
        return;
      }
      armHeartbeat();
      if (frame.type === "heartbeat") {
        call("heartbeat", frame);
        return;
      }
      if (frame.type === "snapshot") call("snapshot", frame);
      else if (frame.type === "section") call("section", frame);
      else if (frame.type === "freshness") call("freshness", frame);
      else if (frame.type === "job") call("job", frame);
    }

    function onEvent(event) {
      var frame = parseFrame(event && event.data);
      dispatch(frame);
    }

    function scheduleSseRetry() {
      clearTimer("reconnect");
      if (!started || hidden) return;
      reconnectTimer = setTimeoutImpl(function () {
        reconnectTimer = null;
        connectSse();
      }, reconnectDelayMs);
    }

    function loseStream() {
      closeSource();
      if (!started || hidden) return;
      status("polling");
      startPolling();
      scheduleSseRetry();
    }

    function connectSse() {
      if (!started || hidden) return;
      closeSource();
      clearTimer("reconnect");
      if (typeof EventSourceCtor !== "function" || !streamUrl) {
        loseStream();
        return;
      }
      try {
        source = new EventSourceCtor(streamUrl, { withCredentials: true });
      } catch (error) {
        source = null;
        loseStream();
        return;
      }
      var instance = source;
      instance.onopen = function () {
        if (source !== instance) return;
        abortPolling();
        clearTimer("reconnect");
        status("sse");
        armHeartbeat();
      };
      instance.onerror = function () {
        if (source !== instance) return;
        loseStream();
      };
      EVENT_NAMES.forEach(function (name) {
        if (typeof instance.addEventListener === "function") {
          instance.addEventListener(name, onEvent);
        }
      });
    }

    function emitFullState(full) {
      if (!validFrame(full)) {
        call("error", publicError(
          "invalid_poll_payload",
          "Les données actualisées sont invalides.",
          "Réessayer"
        ));
        return;
      }
      var now = new Date().toISOString();
      var sections = validFrame(full.sections) ? full.sections : {};
      Object.keys(sections).forEach(function (topic) {
        var value = sections[topic];
        var frame;
        if (validFrame(value) && typeof value.type === "string") {
          frame = value;
        } else {
          frame = {
            type: "snapshot",
            topic: topic,
            data: value,
            collectedAt: now,
            sequence: 0,
            stale: false
          };
        }
        call("snapshot", frame);
      });
      if (Array.isArray(full.jobs)) {
        full.jobs.forEach(function (job) {
          var record = validFrame(job) && validFrame(job.job) ? job.job : job;
          if (validFrame(record)) call("job", { type: "job", job: record });
        });
      }
    }

    function schedulePoll() {
      clearTimer("poll");
      if (!started || hidden || !poll || mode !== "polling") return;
      pollTimer = setTimeoutImpl(pollOnce, pollIntervalMs);
    }

    async function pollOnce() {
      if (!started || hidden || !poll || polling) return;
      var epoch = ++pollEpoch;
      polling = true;
      pollController = typeof AbortController === "function"
        ? new AbortController()
        : null;
      try {
        var payload = await poll({
          signal: pollController ? pollController.signal : undefined
        });
        if (started && !hidden && epoch === pollEpoch) emitFullState(payload);
      } catch (error) {
        if (started && !hidden && epoch === pollEpoch && !(error && error.name === "AbortError")) {
          call("error", publicError(
            "poll_failed",
            "Impossible d’actualiser les données.",
            "Nouvelle tentative automatique"
          ));
        }
      } finally {
        if (epoch === pollEpoch) {
          polling = false;
          pollController = null;
          schedulePoll();
        }
      }
    }

    function startPolling() {
      if (!poll || polling) return;
      clearTimer("poll");
      pollOnce();
    }

    function onVisibilityChange() {
      hidden = Boolean(doc && doc.hidden);
      if (!started) return;
      if (hidden) {
        stopNetwork();
        status("paused");
      } else {
        connectSse();
      }
    }

    function start() {
      if (started) return;
      started = true;
      if (doc && typeof doc.addEventListener === "function") {
        doc.addEventListener("visibilitychange", onVisibilityChange);
      }
      hidden = Boolean(doc && doc.hidden);
      if (hidden) status("paused");
      else connectSse();
    }

    function stop() {
      started = false;
      stopNetwork();
      if (doc && typeof doc.removeEventListener === "function") {
        doc.removeEventListener("visibilitychange", onVisibilityChange);
      }
      status("stopped");
    }

    function reconnect() {
      if (!started) return;
      stopNetwork();
      if (hidden) status("paused");
      else connectSse();
    }

    return {
      start: start,
      stop: stop,
      reconnect: reconnect,
      isStarted: function () { return started; },
      status: function () { return mode; }
    };
  }

  var api = {
    createLiveSubscription: createLiveSubscription,
    subscribeLive: createLiveSubscription,
    evaluateDirectoryPayload: evaluateDirectoryPayload,
    normalizeDirectoryPath: normalizeDirectoryPath
  };
  if (typeof Object.freeze === "function") api = Object.freeze(api);
  root.DashboardLive = api;
  if (root.window && root.window !== root) root.window.DashboardLive = api;
})(typeof window !== "undefined" ? window : globalThis);
