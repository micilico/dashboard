async function fetchWithRetry(url, options = {}, maxRetries = 3) {
  if (options.retry === false) maxRetries = 0;
  const timeoutMs = options.timeout || 10000;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      const mergedOptions = { ...options, signal: controller.signal };
      delete mergedOptions.timeout;
      delete mergedOptions.retry;
      const response = await fetch(url, mergedOptions);
      clearTimeout(timeout);

      if (response.ok || response.status < 500) {
        if (typeof hideReconnectNotice === 'function') {
          hideReconnectNotice();
        }
        return response;
      }

      if (attempt < maxRetries) {
        if (attempt === 0 && typeof showReconnectNotice === 'function') {
          showReconnectNotice();
        }
        await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt)));
      } else {
        if (typeof hideReconnectNotice === 'function') {
          hideReconnectNotice();
        }
        return response;
      }
    } catch (error) {
      if (attempt === maxRetries) {
        if (typeof hideReconnectNotice === 'function') {
          hideReconnectNotice();
        }
        throw error;
      }
      if (attempt === 0 && typeof showReconnectNotice === 'function') {
        showReconnectNotice();
      }
      await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt)));
    }
  }
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

  async function refreshSession() {
    for (let attempt = 0; attempt < refreshAttempts; attempt += 1) {
      try {
        const payload = await request(sessionPath, { cache: "no-store" }, false);
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
        if (attempt === refreshAttempts - 1) throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, 1000 * (attempt + 1)));
    }
    throw Object.assign(new Error("Impossible de renouveler la session de protection."), {
      code: "csrf_refresh_failed",
      recovery: "Actualiser la page",
    });
  }

  request = async (path, options = {}, retryCsrf = true) => {
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    if (options.body && !(options.body instanceof FormData) && !(options.body instanceof URLSearchParams) && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    if ((options.method || "GET").toUpperCase() !== "GET" && csrfHeader) {
      headers.set(csrfHeader, getCsrfToken?.() || "");
    }
    const response = await fetchWithRetry(path, { ...fetchOptions, ...options, headers, credentials: "same-origin" });
    const payload = await response.json().catch(() => ({}));
    if (response.ok) return payload;
    const detail = typeof payload.detail === "object" && payload.detail ? payload.detail : {};
    const error = new Error(detail.message || payload.detail || "Action impossible pour le moment.");
    error.code = detail.code || `http_${response.status}`;
    error.recovery = detail.recovery || "Réessayer";
    error.status = response.status;
    if (response.status === 403 && error.code === "csrf_expired" && retryCsrf) {
      await refreshSession();
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
