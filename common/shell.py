from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


SHELL_START = "<!-- dashboard-shell:start"
SHELL_END = "<!-- dashboard-shell:end -->"


@dataclass(frozen=True)
class NavItem:
    key: str
    element_id: str
    label: str
    href: str
    icon: str
    primary: bool = True


NAV_ITEMS = (
    NavItem(
        "home",
        "homeLink",
        "Vue d’ensemble",
        "/torrent-panel/?view=home",
        '<path d="M3.75 10.5 12 4l8.25 6.5v8.25a1.5 1.5 0 0 1-1.5 1.5h-3.75V13.5h-6v6.75H5.25a1.5 1.5 0 0 1-1.5-1.5z"></path>',
    ),
    NavItem(
        "torrent",
        "torrentLink",
        "Torrents",
        "/torrent-panel/",
        '<path d="M12 3v12m0 0 4-4m-4 4-4-4M5 17.25v1.5A2.25 2.25 0 0 0 7.25 21h9.5A2.25 2.25 0 0 0 19 18.75v-1.5"></path>',
    ),
    NavItem(
        "prowlarr",
        "prowlarrLink",
        "Prowlarr",
        "/prowlarr-panel/",
        '<circle cx="11" cy="11" r="6.5"></circle><path d="m16 16 4.25 4.25"></path>',
    ),
    NavItem(
        "cloud",
        "cloudLink",
        "Cloud",
        "/cloud-panel/",
        '<path d="M8.5 18.25h8.25a3.25 3.25 0 0 0 .6-6.44A4.75 4.75 0 0 0 8 10.5a3.5 3.5 0 0 0 .5 6.94Z"></path>',
    ),
    NavItem(
        "media",
        "mediaLink",
        "Médias",
        "/media-panel/",
        '<rect x="4" y="4" width="16" height="16" rx="3"></rect><path d="M8 9h8M8 12h5M8 15h8"></path>',
        primary=False,
    ),
    NavItem(
        "storage",
        "storageLink",
        "Système",
        "/storage-panel/",
        '<rect x="4" y="5" width="16" height="5" rx="1.5"></rect><rect x="4" y="14" width="16" height="5" rx="1.5"></rect><path d="M8 7.5h.01M8 16.5h.01"></path>',
        primary=False,
    ),
    NavItem(
        "health",
        "healthLink",
        "Santé",
        "/health/",
        '<path d="M12 4.75 18.5 7v5.25c0 4.2-2.6 6.8-6.5 7.99-3.9-1.19-6.5-3.79-6.5-7.99V7z"></path><path d="M9 12h6M12 9v6"></path>',
        primary=False,
    ),
    NavItem(
        "activity",
        "activityLink",
        "Activité",
        "/activity/",
        '<path d="M4 13.5h3l2.25-6 4.5 10 2.25-4H20"></path>',
        primary=False,
    ),
)


SHELL_DEFAULTS = {
    "home": {
        "status": "Vérification en cours",
        "detail": "Connexion aux services en cours.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
        "home_badge": True,
        "brand_href": "./?view=home",
    },
    "torrent": {
        "status": "Vérification en cours",
        "detail": "Connexion aux services en cours.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
        "home_badge": True,
    },
    "prowlarr": {
        "status": "Connexion prête",
        "detail": "Synchronisation Prowlarr.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
    },
    "cloud": {
        "status": "Stockage prêt",
        "detail": "Navigation fichiers active.",
        "meta_label": "Chemin courant",
        "meta_value": "/mnt/ultra-media",
    },
    "activity": {
        "status": "Service prêt",
        "detail": "Surveillance en direct.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
    },
    "storage": {
        "status": "Service prêt",
        "detail": "Surveillance en direct.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
    },
    "media": {
        "status": "Service prêt",
        "detail": "Surveillance en direct.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
    },
    "health": {
        "status": "Service prêt",
        "detail": "Surveillance en direct.",
        "meta_label": "Dernière mise a jour",
        "meta_id": "refreshStatus",
        "time_id": "lastCheck",
    },
}


def _brand() -> str:
    return """        <div class="brand-block">
          <a class="brand-mark" href="{brand_href}" aria-label="Retour a la vue d'ensemble">
            <svg viewBox="0 0 64 64" aria-hidden="true">
              <polygon points="18,14 41,14 50,30 27,30" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" opacity="0.7"/>
              <polygon points="14,34 37,34 46,50 23,50" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round"/>
              <polygon points="27,30 41,30 37,34 23,34" fill="var(--accent)" opacity="0.15"/>
            </svg>
          </a>
          <div class="brand-copy">
            <p class="eyebrow">Centre de contrôle</p>
            <p class="product-name">Dashboard</p>
          </div>
        </div>"""


def _nav_link(item: NavItem, current: str, *, home_badge: bool = False) -> str:
    current_attr = ' aria-current="page"' if item.key == current else ""
    badge = ""
    if home_badge and item.key == "home":
        badge = '\n            <span id="navAlertCount" class="nav-count" hidden>0</span>'
    return f"""          <a id="{item.element_id}"{current_attr} href="{item.href}" data-shell-nav="{item.key}">
            <span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24">{item.icon}</svg></span>
            <span class="nav-label">{item.label}</span>{badge}
          </a>"""


def _nav(current: str, *, home_badge: bool = False) -> str:
    primary = [_nav_link(item, current, home_badge=home_badge) for item in NAV_ITEMS if item.primary]
    overflow = [_nav_link(item, current, home_badge=home_badge) for item in NAV_ITEMS if not item.primary]
    overflow_links = "\n".join(overflow)
    return f"""        <nav class="nav" aria-label="Navigation principale">
{chr(10).join(primary)}
          <div class="nav-more">
            <button class="nav-more-toggle" type="button" aria-expanded="false" aria-controls="nav-more-panel" aria-label="Afficher les destinations secondaires">
              <span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h.01M12 12h.01M19 12h.01"></path></svg></span>
              <span class="nav-label">Plus</span>
            </button>
            <div class="nav-more-panel" id="nav-more-panel" hidden>
{overflow_links}
            </div>
          </div>
        </nav>"""


def _status(config: dict[str, object]) -> str:
    meta_id = f' id="{config["meta_id"]}"' if config.get("meta_id") else ""
    time_id = f' id="{config["time_id"]}"' if config.get("time_id") else ""
    value = config.get("meta_value", "")
    datetime_attr = ' datetime=""' if config.get("time_id") else ""
    return f"""        <div class="topbar-actions sidebar-footer">
          <div class="sidebar-health" aria-live="polite">
            <div class="sidebar-health-row">
              <span class="status-dot"></span>
              <span id="statusText">{config["status"]}</span>
            </div>
            <p id="sidebarStatusDetail">{config["detail"]}</p>
          </div>
          <div class="status-pill"{meta_id} aria-live="polite">
            <span>{config["meta_label"]}</span>
            <time{time_id}{datetime_attr}>{value}</time>
          </div>
        </div>"""


def render_shell(current: str) -> str:
    config = dict(SHELL_DEFAULTS[current])
    brand = _brand().format(brand_href=config.get("brand_href", "/torrent-panel/?view=home"))
    nav = _nav(current, home_badge=bool(config.get("home_badge")))
    status = _status(config)
    return f"""      {SHELL_START} current="{current}" -->
      <header class="topbar">
{brand}
{nav}
{status}
      </header>
      {SHELL_END}"""


def render_shell_into_html(html: str, current: str) -> str:
    shell = render_shell(current)
    marker_pattern = re.compile(
        rf"\s*{re.escape(SHELL_START)}[^>]*-->\s*<header class=\"topbar\">[\s\S]*?</header>\s*{re.escape(SHELL_END)}"
    )
    if marker_pattern.search(html):
        return marker_pattern.sub("\n" + shell, html, count=1)

    header_pattern = re.compile(r"\s*<header class=\"topbar\">[\s\S]*?</header>")
    if not header_pattern.search(html):
        raise ValueError("No dashboard shell header found")
    return header_pattern.sub("\n" + shell, html, count=1)


def render_shell_files(static_root: Path, pages: dict[str, str]) -> None:
    for relative_path, current in pages.items():
        path = static_root / relative_path
        html = path.read_text(encoding="utf-8")
        updated = render_shell_into_html(html, current)
        if updated != html:
            path.write_text(updated, encoding="utf-8")
