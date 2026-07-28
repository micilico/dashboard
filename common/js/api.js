async function fetchWithRetry(url, options = {}, maxRetries = 3) {
  const timeoutMs = options.timeout || 10000;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      const mergedOptions = { ...options, signal: controller.signal };
      delete mergedOptions.timeout;
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
