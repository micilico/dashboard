const DASHBOARD_NAV_DEFAULT_PREFIXES = {
  torrent: "/torrent-panel",
  prowlarr: "/prowlarr-panel",
  cloud: "/cloud-panel",
  activity: "/activity",
  storage: "/storage-panel",
  media: "/media-panel",
  health: "/health",
  stats: "/stats-panel",
};

function normalizeDashboardPrefix(value, fallback = "") {
  const prefix = String(value || fallback || "").replace(/\/$/, "");
  return prefix === "/" ? "" : prefix;
}

function dashboardPrefixConfig(config = {}) {
  return {
    torrent: normalizeDashboardPrefix(config.torrentPanelPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.torrent),
    prowlarr: normalizeDashboardPrefix(config.prowlarrPanelPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.prowlarr),
    cloud: normalizeDashboardPrefix(config.cloudPanelPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.cloud),
    activity: normalizeDashboardPrefix(config.activityPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.activity),
    storage: normalizeDashboardPrefix(config.storagePrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.storage),
    media: normalizeDashboardPrefix(config.mediaPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.media),
    health: normalizeDashboardPrefix(config.healthPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.health),
    stats: normalizeDashboardPrefix(config.statsPrefix, DASHBOARD_NAV_DEFAULT_PREFIXES.stats),
  };
}

function dashboardNavHref(key, prefixes) {
  const withSlash = (prefix) => `${prefix || ""}/`;
  if (key === "home") return `${withSlash(prefixes.torrent)}?view=home`;
  if (key === "torrent") return withSlash(prefixes.torrent);
  if (key === "prowlarr") return withSlash(prefixes.prowlarr);
  if (key === "cloud") return withSlash(prefixes.cloud);
  if (key === "media") return withSlash(prefixes.media);
  if (key === "storage") return withSlash(prefixes.storage);
  if (key === "health") return withSlash(prefixes.health);
  if (key === "activity") return withSlash(prefixes.activity);
  if (key === "stats") return withSlash(prefixes.stats);
  return "";
}

function configureDashboardNavigation(config = {}, current = "") {
  const prefixes = dashboardPrefixConfig(config);
  const links = document.querySelectorAll(".nav [data-shell-nav]");
  links.forEach((link) => {
    const key = link.dataset.shellNav;
    const href = dashboardNavHref(key, prefixes);
    if (href) link.href = href;
    if (current) {
      if (key === current) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
  });
  return prefixes;
}

function configureMoreNavigation() {
  document.querySelectorAll(".nav-more-toggle").forEach((toggle) => {
    if (toggle.dataset.bound === "true") return;
    toggle.dataset.bound = "true";
    const panel = document.getElementById(toggle.getAttribute("aria-controls"));
    if (!panel) return;
    const setOpen = (open) => {
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.closest(".nav-more")?.classList.toggle("is-open", open);
      panel.setAttribute("aria-hidden", open ? "false" : "true");
    };
    toggle.addEventListener("click", () => setOpen(!toggle.closest(".nav-more")?.classList.contains("is-open")));
    document.addEventListener("click", (event) => {
      if (!toggle.closest(".nav-more")?.contains(event.target)) setOpen(false);
    });
    panel.addEventListener("click", () => setOpen(false));
  });
}

const DASHBOARD_PREFETCHED = new Set();

function dashboardPrefetch(href) {
  if (!href || !/^https?:/.test(href) || DASHBOARD_PREFETCHED.has(href)) return;
  DASHBOARD_PREFETCHED.add(href);
  try {
    const link = document.createElement("link");
    link.rel = "prefetch";
    link.href = href;
    document.head.appendChild(link);
  } catch {
    /* prefetch is best-effort */
  }
}

function configurePrefetchOnHover() {
  if (document.body?.dataset?.dashboardPrefetchBound === "true") return;
  if (!document.body) return;
  document.body.dataset.dashboardPrefetchBound = "true";
  document.addEventListener("mouseover", (event) => {
    const link = event.target && typeof event.target.closest === "function"
      ? event.target.closest(".nav a[data-shell-nav]")
      : null;
    if (link && link.href && link.getAttribute("aria-current") !== "page") {
      dashboardPrefetch(link.href);
    }
  }, { passive: true });
}

if (typeof window !== "undefined") {
  window.DashboardNavigation = {
    configure: configureDashboardNavigation,
    configureMore: configureMoreNavigation,
    prefetch: dashboardPrefetch,
    configurePrefetch: configurePrefetchOnHover,
    href: dashboardNavHref,
    normalizePrefix: normalizeDashboardPrefix,
    prefixes: dashboardPrefixConfig,
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      configureMoreNavigation();
      configurePrefetchOnHover();
    }, { once: true });
  } else {
    configureMoreNavigation();
    configurePrefetchOnHover();
  }
}
