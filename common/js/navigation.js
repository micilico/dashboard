const DASHBOARD_NAV_DEFAULT_PREFIXES = {
  torrent: "/torrent-panel",
  prowlarr: "/prowlarr-panel",
  cloud: "/cloud-panel",
  activity: "/activity",
  storage: "/storage-panel",
  media: "/media-panel",
  health: "/health",
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
      panel.hidden = !open;
      toggle.closest(".nav-more")?.classList.toggle("is-open", open);
    };
    toggle.addEventListener("click", () => setOpen(panel.hidden));
    document.addEventListener("click", (event) => {
      if (!toggle.closest(".nav-more")?.contains(event.target)) setOpen(false);
    });
    panel.addEventListener("click", () => setOpen(false));
  });
}

if (typeof window !== "undefined") {
  window.DashboardNavigation = {
    configure: configureDashboardNavigation,
    configureMore: configureMoreNavigation,
    href: dashboardNavHref,
    normalizePrefix: normalizeDashboardPrefix,
    prefixes: dashboardPrefixConfig,
  };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", configureMoreNavigation, { once: true });
  } else {
    configureMoreNavigation();
  }
}
