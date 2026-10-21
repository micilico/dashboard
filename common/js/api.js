const DEFAULT_REQUEST_TIMEOUT_MS = 10000;

function createAbortError(signal) {
  const reason = signal?.reason;
  if (reason && typeof reason === "object" && reason.name === "AbortError") return reason;
  if (typeof DOMException === "function") return new DOMException("The operation was aborted.", "AbortError");
  const error = new Error("The operation was aborted.");
  error.name = "AbortError";
  return error;
}

function throwIfAborted(signal) {
  if (signal?.aborted) throw createAbortError(signal);
}

function isAbortError(error) {
  return error?.name === "AbortError";
}

function createTimeoutError() {
  const error = new Error("Le service ne répond pas à temps.");
  error.name = "TimeoutError";
  error.code = "request_timeout";
  error.recovery = "Réessayer";
  return error;
}

function delayWithSignal(delayMs, signal) {
  throwIfAborted(signal);
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(createAbortError(signal));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, delayMs);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function createAttemptSignal(callerSignal, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;

  const abortFromCaller = () => controller.abort(createAbortError(callerSignal));
  if (callerSignal?.aborted) {
    abortFromCaller();
  } else {
    callerSignal?.addEventListener("abort", abortFromCaller, { once: true });
  }

  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort(createAbortError());
  }, timeoutMs);

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    cleanup: () => {
      window.clearTimeout(timer);
      callerSignal?.removeEventListener("abort", abortFromCaller);
    },
  };
}

function showReconnectNoticeIfAvailable() {
  if (typeof showReconnectNotice === "function") showReconnectNotice();
}

function hideReconnectNoticeIfAvailable() {
  if (typeof hideReconnectNotice === "function") hideReconnectNotice();
}

async function waitBeforeRetry(attempt, signal) {
  showReconnectNoticeIfAvailable();
  try {
    await delayWithSignal(1000 * Math.pow(2, attempt), signal);
  } catch (error) {
    hideReconnectNoticeIfAvailable();
    throw error;
  }
}

async function fetchWithRetryHandled(url, options, maxRetries, consumeResponse) {
  const callerSignal = options.signal;
  const requestOptions = { ...options };
  delete requestOptions.timeout;
  delete requestOptions.retry;
  const method = String(requestOptions.method || "GET").toUpperCase();
  const retryIsSafe = ["GET", "HEAD", "OPTIONS"].includes(method) || options.retry === true;
  const configuredRetries = Number.isFinite(options.retry) ? options.retry : maxRetries;
  const retries = options.retry === false || !retryIsSafe
    ? 0
    : Math.max(0, Number.isFinite(configuredRetries) ? configuredRetries : 3);
  const timeoutMs = Number(options.timeout) > 0 ? Number(options.timeout) : DEFAULT_REQUEST_TIMEOUT_MS;

  throwIfAborted(callerSignal);

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const attemptSignal = createAttemptSignal(callerSignal, timeoutMs);
    try {
      const response = await fetch(url, { ...requestOptions, signal: attemptSignal.signal });
      const handledResponse = await consumeResponse(response);
      throwIfAborted(callerSignal);

      if (response.ok || response.status < 500) {
        hideReconnectNoticeIfAvailable();
        return handledResponse;
      }

      if (attempt < retries) {
        await waitBeforeRetry(attempt, callerSignal);
      } else {
        hideReconnectNoticeIfAvailable();
        return handledResponse;
      }
    } catch (error) {
      if (callerSignal?.aborted) {
        hideReconnectNoticeIfAvailable();
        throw createAbortError(callerSignal);
      }

      if (attemptSignal.didTimeout()) {
        if (attempt < retries) {
          await waitBeforeRetry(attempt, callerSignal);
        } else {
          hideReconnectNoticeIfAvailable();
          throw createTimeoutError();
        }
      } else if (isAbortError(error)) {
        hideReconnectNoticeIfAvailable();
        throw error;
      } else if (attempt < retries) {
        await waitBeforeRetry(attempt, callerSignal);
      } else {
        hideReconnectNoticeIfAvailable();
        throw error;
      }
    } finally {
      attemptSignal.cleanup();
    }
  }
}

async function fetchWithRetry(url, options = {}, maxRetries = 3) {
  return fetchWithRetryHandled(url, options, maxRetries, (response) => response);
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const index = Math.min(Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function describeError(error, fallback = "Action impossible pour le moment.", recovery = "Réessayer") {
  const message = error?.message || fallback;
  const action = error?.recovery || recovery;
  return `${message} ${action}.`;
}

function showErrorMessage(container, error, options = {}) {
  if (!container) return;
  const target = options.messageElement || container;
  target.textContent = describeError(error, options.fallback, options.recovery);
  container.hidden = false;
}

function createToast(elementOrGetter, duration = 4200) {
  let timer;
  return (message) => {
    const element = typeof elementOrGetter === "function" ? elementOrGetter() : elementOrGetter;
    if (!element) return;
    element.textContent = message;
    element.hidden = false;
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { element.hidden = true; }, duration);
  };
}

function createApiClient({
  csrfHeader,
  getCsrfToken,
  setCsrfToken,
  sessionPath,
  fetchOptions = {},
  refreshAttempts = 1,
} = {}) {
  let request;

  async function refreshSession(signal) {
    for (let attempt = 0; attempt < refreshAttempts; attempt += 1) {
      try {
        const payload = await request(sessionPath, { cache: "no-store", signal }, false);
        const token = payload?.csrfToken || "";
        if (token) {
          setCsrfToken?.(token);
          return token;
        }
        const error = new Error("Impossible de renouveler la session de protection.");
        error.code = "csrf_refresh_failed";
        error.recovery = "Actualiser la page";
        throw error;
      } catch (error) {
        if (signal?.aborted || isAbortError(error)) throw createAbortError(signal || { aborted: true });
        if (attempt === refreshAttempts - 1) throw error;
      }
      await delayWithSignal(1000 * (attempt + 1), signal);
    }
    throw Object.assign(new Error("Impossible de renouveler la session de protection."), {
      code: "csrf_refresh_failed",
      recovery: "Actualiser la page",
    });
  }

  // Responses are single-use and callers may mutate payloads, so concurrent
  // GETs are intentionally not coalesced by this shared client.
  request = async (path, options = {}, retryCsrf = true) => {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !(options.body instanceof FormData) && !(options.body instanceof URLSearchParams) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if ((options.method || "GET").toUpperCase() !== "GET" && csrfHeader) {
      headers.set(csrfHeader, getCsrfToken?.() || "");
    }

    const requestOptions = { ...fetchOptions, ...options, headers, credentials: "same-origin" };
    const { response, payload } = await fetchWithRetryHandled(
      path,
      requestOptions,
      3,
      async (rawResponse) => {
        let parsedPayload = {};
        try {
          parsedPayload = await rawResponse.json();
        } catch (error) {
          if (isAbortError(error)) throw error;
        }
        return { response: rawResponse, payload: parsedPayload };
      },
    );

    if (response.ok) return payload;
    const detail = typeof payload.detail === "object" && payload.detail ? payload.detail : {};
    const error = new Error(detail.message || payload.detail || "Action impossible pour le moment.");
    error.code = detail.code || `http_${response.status}`;
    error.recovery = detail.recovery || "Réessayer";
    error.status = response.status;
    if (response.status === 403 && error.code === "csrf_expired" && retryCsrf) {
      await refreshSession(options.signal);
      return request(path, options, false);
    }
    throw error;
  };

  return { request, refreshSession };
}

function showReconnectNotice() {
  let banner = document.getElementById('reconnect-banner');
  if (!banner) {
    banner = document.createElement('div');
    banner.id = 'reconnect-banner';
    banner.className = 'reconnect-banner';
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.textContent = 'Tentative de reconnexion…';
    document.body.prepend(banner);
  }
}

function hideReconnectNotice() {
  const banner = document.getElementById('reconnect-banner');
  if (banner) banner.remove();
}

if (typeof window !== 'undefined') {
  window.fetchWithRetry = fetchWithRetry;
  window.showReconnectNotice = showReconnectNotice;
  window.hideReconnectNotice = hideReconnectNotice;
}
