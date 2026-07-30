(() => {
  const launcherRoutes = new Set([
    "/torrent-panel/",
    "/prowlarr-panel/",
    "/cloud-panel/",
    "/media-panel/",
    "/storage-panel/",
    "/health/",
    "/activity/",
  ]);

  function normalizePath(href) {
    try {
      const url = new URL(href, window.location.origin);
      return url.pathname.endsWith("/") ? url.pathname : `${url.pathname}/`;
    } catch {
      return "";
    }
  }

  function enhanceLinks() {
    document.querySelectorAll("a[href]").forEach((link) => {
      const path = normalizePath(link.getAttribute("href"));
      if (launcherRoutes.has(path)) {
        link.classList.add("dashboard-launcher-link");
        if (!link.getAttribute("aria-label")) {
          link.setAttribute("aria-label", `Ouvrir ${link.textContent.trim() || path}`);
        }
      }

      if (link.target === "_blank") {
        const rel = new Set((link.rel || "").split(/\s+/).filter(Boolean));
        rel.add("noopener");
        rel.add("noreferrer");
        link.rel = [...rel].join(" ");
      }
    });
  }

  function markReady() {
    document.documentElement.lang = "fr";
    document.title = "Centre de contrôle";
    document.body.classList.add("dashboard-homepage-ready");
    enhanceLinks();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", markReady, { once: true });
  } else {
    markReady();
  }

  const observer = new MutationObserver(enhanceLinks);
  observer.observe(document.documentElement, { childList: true, subtree: true });
})();
