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
        return response;
      }

      if (attempt < maxRetries) {
        if (attempt === 0 && typeof showReconnectNotice === 'function') {
          showReconnectNotice();
        }
        await new Promise(resolve => setTimeout(resolve, 1000 * Math.pow(2, attempt)));
      } else {
        return response;
      }
    } catch (error) {
      if (attempt === maxRetries) throw error;
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
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:100;padding:10px 16px;background:rgba(245,158,11,0.9);color:#000;text-align:center;font-weight:700;';
    banner.textContent = 'Tentative de reconnexion...';
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
