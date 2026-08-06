const consoleConfig = window.__DASHBOARD_CONSOLE_CONFIG__ || {};

const state = {
  section: consoleConfig.section || "activity",
  publicPrefix: consoleConfig.publicPrefix || "",
  apiPrefix: consoleConfig.apiPrefix || "/api",
  torrentPanelPrefix: consoleConfig.torrentPanelPrefix || "/torrent-panel",
  prowlarrPanelPrefix: consoleConfig.prowlarrPanelPrefix || "/prowlarr-panel",
  cloudPanelPrefix: consoleConfig.cloudPanelPrefix || "/cloud-panel",
  activityPrefix: consoleConfig.activityPrefix || "/activity",
  storagePrefix: consoleConfig.storagePrefix || "/storage-panel",
  mediaPrefix: consoleConfig.mediaPrefix || "/media-panel",
  healthPrefix: consoleConfig.healthPrefix || "/health",
  statsPrefix: consoleConfig.statsPrefix || "/stats-panel",
  csrfToken: "",
};

const els = {
  title: document.querySelector("#pageTitle"),
  subtitle: document.querySelector("#pageSubtitle"),
  pageStatus: document.querySelector("#pageStatus"),
  pageEyebrow: document.querySelector("#pageEyebrow"),
  refreshStatus: document.querySelector("#refreshStatus"),
  lastCheck: document.querySelector("#lastCheck"),
  refreshButton: document.querySelector("#refreshButton"),
  primaryButton: document.querySelector("#primaryButton"),
  summaryGrid: document.querySelector("#summaryGrid"),
  contentA: document.querySelector("#contentA"),
  contentB: document.querySelector("#contentB"),
  cardsGrid: document.querySelector("#cardsGrid"),
  toast: document.querySelector("#toast"),
  homeLink: document.querySelector("#homeLink"),
  activityLink: document.querySelector("#activityLink"),
  torrentLink: document.querySelector("#torrentLink"),
  prowlarrLink: document.querySelector("#prowlarrLink"),
  cloudLink: document.querySelector("#cloudLink"),
  storageLink: document.querySelector("#storageLink"),
  mediaLink: document.querySelector("#mediaLink"),
  healthLink: document.querySelector("#healthLink"),
  statusText: document.querySelector("#statusText"),
  sidebarStatusDetail: document.querySelector("#sidebarStatusDetail"),
};
const { element, state: systemState } = window.DashboardDOM;

function text(value, fallback = "—") {
  const cleaned = String(value ?? "").trim();
  return cleaned || fallback;
}

const showToast = createToast(() => els.toast);

function formatDate(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function badge(label, kind = "info") {
  const span = document.createElement("span");
  span.className = `badge ${kind}`;
  span.textContent = label;
  return span;
}

function card(label, value, hint = "", className = "") {
  const article = document.createElement("article");
  article.className = `stat panel ${className}`.trim();
  const top = document.createElement("span");
  top.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  article.append(top, strong);
  if (hint) article.append(Object.assign(document.createElement("p"), { className: "muted", textContent: hint }));
  return article;
}

function panel(title, subtitle, bodyChildren = [], actions = []) {
  const section = element("section", { className: "panel" });
  const heading = element("div", {
    children: [
      element("h2", { text: title }),
      subtitle ? element("p", { className: "muted", text: subtitle }) : null,
    ].filter(Boolean),
  });
  const head = element("div", { className: "panel-head", children: [heading, ...actions] });
  const body = element("div", { className: "panel-body", children: bodyChildren });
  section.append(head, body);
  return section;
}

function listContainer(id, className = "list") {
  return element("div", { className, attrs: { id } });
}

function healthTable(rows) {
  const table = element("table", { className: "data-table" });
  const headRow = element("tr", {
    children: ["Service", "Liveness", "Readiness", "Dernier succès", "Message"].map((label) => element("th", { text: label })),
  });
  const tbody = element("tbody", { attrs: { id: "healthRows" }, children: rows });
  table.append(element("thead", { children: [headRow] }), tbody);
  return element("div", { className: "table-wrap table-scroll", children: [table] });
}

function setRefreshStamp() {
  const now = new Date();
  if (els.lastCheck) {
    els.lastCheck.textContent = now.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
    els.lastCheck.dateTime = now.toISOString();
  }
}

function setSidebarStatus(label, detail, tone = "operational") {
  if (els.statusText) els.statusText.textContent = label;
  if (els.sidebarStatusDetail) els.sidebarStatusDetail.textContent = detail;
  const dot = document.querySelector(".sidebar-health .status-dot");
  if (dot?.classList) {
    dot.classList.remove("degraded", "checking", "unavailable");
    if (tone === "degraded") dot.classList.add("degraded");
    if (tone === "checking") dot.classList.add("checking");
    if (tone === "unavailable") dot.classList.add("unavailable");
  }
}

function setPageStatus(message, tone = "info") {
  if (!els.pageStatus) return;
  els.pageStatus.hidden = !message;
  els.pageStatus.textContent = message || "";
  els.pageStatus.className = `page-status ${tone}`.trim();
}

function configureSummaryGrid(mode) {
  els.summaryGrid.className = `summary-grid summary-grid-console ${mode}`.trim();
}

function configureContentGrid(mode) {
  const layout = mode ? ` layout-${mode}` : "";
  document.querySelector(".content-grid").className = `content-grid${layout}`;
}

const consoleApiClient = createApiClient({
  csrfHeader: "X-Torrent-Panel-CSRF",
  getCsrfToken: () => state.csrfToken,
  setCsrfToken: (token) => { state.csrfToken = token; },
  sessionPath: `${state.apiPrefix}/session`,
});
const api = consoleApiClient.request;
const refreshSession = consoleApiClient.refreshSession;

function configureLinks() {
  window.DashboardNavigation?.configure(state, state.section);
  els.torrentLink.href = `${state.torrentPanelPrefix}/?view=torrents`;
}

function renderList(container, items, emptyText) {
  container.replaceChildren(...(items.length ? items : [
    systemState({ type: "empty", title: emptyText, compact: true }),
  ]));
}

async function postJson(path, payload, successMessage) {
  await api(path, { method: "POST", body: JSON.stringify(payload) });
  showToast(successMessage);
  await load();
}

async function renderActivity() {
  configureSummaryGrid("summary-grid-trio");
  configureContentGrid("8-4");
  els.title.textContent = "Centre d’activité";
  els.subtitle.textContent = "Synthèse transverse des services, des alertes et des simulations d’automatisation.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Journal";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = await api(`${state.apiPrefix}/activity`, { cache: "no-store" });
  const summary = payload.summary || {};
  els.summaryGrid.replaceChildren(
    card("Téléchargements actifs", String(summary.downloadsActive || 0)),
    card("Vitesse descendante", `${formatBytes(summary.downloadSpeedBytes || 0)}/s`),
    card("Alertes actives", String((payload.alerts || []).length)),
  );
  setSidebarStatus(
    (payload.alerts || []).length ? `${(payload.alerts || []).length} alerte(s) active(s)` : "Activité sous contrôle",
    `${(payload.timeline || []).length} événement(s) consolidé(s).`,
    (payload.alerts || []).length ? "degraded" : "operational",
  );
  setPageStatus((payload.alerts || []).length ? "Des alertes nécessitent un suivi." : "Aucune alerte active sur la chronologie.", (payload.alerts || []).length ? "warn" : "ok");

  const timelineItems = (payload.timeline || []).map((item) => {
    const article = document.createElement("article");
    article.className = "timeline-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: `${text(item.service)} · ${text(item.type)}` }),
      Object.assign(document.createElement("div"), { textContent: text(item.message) }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: `${formatDate(item.date)} · ${text(item.result)} · ${text(item.origin)}` }),
    );
    return article;
  });

  const alertItems = (payload.alerts || []).map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    const actions = document.createElement("div");
    actions.className = "actions";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "button";
    toggle.textContent = item.status === "acknowledged" ? "Rouvrir" : "Acquitter";
    toggle.onclick = () => postJson(
      `${state.apiPrefix}/notifications/${item.status === "acknowledged" ? "reopen" : "ack"}`,
      { code: item.code },
      item.status === "acknowledged" ? "Alerte rouverte." : "Alerte acquittée.",
    );
    actions.append(toggle);
    article.append(
      Object.assign(document.createElement("strong"), { textContent: `${text(item.service)} · ${text(item.status)}` }),
      Object.assign(document.createElement("div"), { textContent: text(item.message) }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: `Première occurrence: ${formatDate(item.firstSeenAt)} · Dernière occurrence: ${formatDate(item.lastSeenAt)} · ${item.occurrences || 0} occurrence(s)` }),
      actions,
    );
    return article;
  });

  const simulationCards = (payload.simulations || []).map((item) => {
    const article = document.createElement("article");
    article.className = "card";
    article.append(
      Object.assign(document.createElement("h3"), { textContent: text(item.name) }),
      Object.assign(document.createElement("p"), { className: "muted", textContent: `Déclencheur: ${text(item.trigger)} · ${item.matched ? "correspondance détectée" : "aucune correspondance"}` }),
      badge(item.matched ? "Simulation correspondante" : "Simulation inactive", item.matched ? "warn" : "info"),
    );
    return article;
  });

  els.contentA.replaceChildren(
    panel("Chronologie récente", "Événements consolidés sans secret.", [listContainer("timelineList", "timeline")]),
  );
  renderList(document.querySelector("#timelineList"), timelineItems, "Aucun événement récent.");

  els.contentB.replaceChildren(
    panel("Notifications", "Déduplication, acquittement et réouverture.", [listContainer("notificationList")]),
  );
  renderList(document.querySelector("#notificationList"), alertItems, "Aucune alerte active.");

  els.cardsGrid.replaceChildren(...simulationCards);
}

async function renderStorage() {
  configureSummaryGrid("summary-grid-storage");
  configureContentGrid("12");
  els.title.textContent = "Panneau de stockage";
  els.subtitle.textContent = "État du montage, statistiques rclone et seuils d’occupation.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Système";
  els.primaryButton.textContent = "Actualiser rclone";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async () => {
    await postJson(`${state.apiPrefix}/media-actions/rclone-refresh`, {}, "Actualisation rclone lancée.");
  };
  const payload = await api(`${state.apiPrefix}/storage`, { cache: "no-store" });
  const disk = payload.disk || {};
  const rclone = payload.rclone || {};
  if (Number(disk.usedBytes) > 0) {
    window.DashboardDOM?.updateDiskRing({ percent: disk.usedPercent, usedBytes: disk.usedBytes, totalBytes: disk.totalBytes });
  } else {
    window.DashboardDOM?.hideDiskRing();
  }
  els.summaryGrid.replaceChildren(
    card("Capacité totale", formatBytes(disk.totalBytes || 0)),
    card("Utilisé", formatBytes(disk.usedBytes || 0), `${disk.usedPercent || 0} %`),
    card("Disponible", formatBytes(disk.freeBytes || 0), `${disk.freePercent || 0} %`),
    card("Vitesse rclone", rclone.speedLabel || "0 o/s", "", "stat-span-6 stat-compact"),
    card("Erreurs", String(rclone.errors || 0), "", "stat-span-6 stat-compact"),
  );
  setSidebarStatus(
    disk.mounted ? "Montage opérationnel" : "Montage indisponible",
    disk.path ? `Chemin surveillé: ${disk.path}` : "Surveillance du stockage active.",
    disk.mounted ? "operational" : "unavailable",
  );
  setPageStatus(disk.mounted ? "Montage et seuils surveillés en continu." : "Le montage doit être vérifié.", disk.mounted ? "ok" : "error");

  const transfers = (rclone.transfers || []).map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: text(item.name || item.remote || "Transfert") }),
      Object.assign(document.createElement("div"), { textContent: `Vitesse: ${formatBytes(item.speed || 0)}/s · Taille: ${formatBytes(item.size || 0)}` }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: text(item.group || item.srcFs || "") }),
    );
    return article;
  });

  els.contentA.replaceChildren(
    panel(
      "Montage et seuils",
      `Chemin surveillé: ${text(disk.path)}`,
      [element("p", { className: "muted", text: `Statut: ${text(disk.status)}.` })],
      [badge(disk.mounted ? "Monté" : "Indisponible", disk.mounted ? "ok" : "error")],
    ),
  );

  els.contentB.replaceChildren(
    panel("Transferts actifs", `Dernière réponse réussie: ${formatDate(rclone.lastSuccessfulResponseAt)}`, [listContainer("transferList")]),
  );
  renderList(document.querySelector("#transferList"), transfers, rclone.errorMessage || "Aucun transfert actif.");
  els.cardsGrid.replaceChildren();
}

async function renderMedia() {
  configureContentGrid("7-5");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Panneau médias";
  els.subtitle.textContent = "Vue légère Jellyfin: statut, tâches, lectures et derniers médias ajoutés.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Bibliothèque";
  els.primaryButton.textContent = "Scanner Jellyfin";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async () => {
    await postJson(`${state.apiPrefix}/media-actions/jellyfin-refresh`, {}, "Scan Jellyfin lancé.");
  };
  const payload = await api(`${state.apiPrefix}/media`, { cache: "no-store" });
  els.summaryGrid.replaceChildren(
    card("Serveur", text(payload.serverName), text(payload.version)),
    card("Lectures en cours", String((payload.sessions || []).length)),
    card("Utilisateurs actifs", String((payload.activeUsers || []).length)),
  );
  setSidebarStatus(
    payload.serverName ? `${text(payload.serverName)} disponible` : "Jellyfin indisponible",
    `${(payload.tasks || []).length} tâche(s) observée(s).`,
    payload.serverName ? "operational" : "degraded",
  );
  setPageStatus((payload.errors || []).length ? (payload.errors || []).join(" ") : "Bibliothèque et tâches visibles sans données fictives.", (payload.errors || []).length ? "warn" : "ok");

  const recentItems = (payload.recentItems || []).map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: text(item.name) }),
      Object.assign(document.createElement("div"), { textContent: text(item.type) }),
    );
    return article;
  });
  const tasks = (payload.tasks || []).map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: text(item.name) }),
      Object.assign(document.createElement("div"), { textContent: `${text(item.state)} · ${item.isRunning ? "en cours" : "au repos"}` }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: text(item.lastExecutionResult) }),
    );
    return article;
  });

  els.contentA.replaceChildren(
    panel("Derniers médias ajoutés", "Ouverture native via Jellyfin disponible.", [listContainer("recentMediaList")]),
  );
  renderList(document.querySelector("#recentMediaList"), recentItems, (payload.errors || []).join(" ") || "Aucun média récent.");

  els.contentB.replaceChildren(
    panel("Tâches planifiées", "Tâches et scans observables.", [listContainer("taskList")]),
  );
  renderList(document.querySelector("#taskList"), tasks, "Aucune tâche visible.");
  els.cardsGrid.replaceChildren();
}

async function renderHealth() {
  configureContentGrid("8-4");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Santé du système";
  els.subtitle.textContent = "Liveness, readiness et état global des services exposés via le backend contrôlé.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Observabilité";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = await api(`${state.apiPrefix}/health/overview`, { cache: "no-store" });
  const summary = payload.summary || {};
  els.summaryGrid.replaceChildren(
    card("État global", text(payload.globalStatus)),
    card("Opérationnels", String(summary.operational || 0)),
    card("Incidents", String((summary.degraded || 0) + (summary.unavailable || 0) + (payload.alerts || []).length)),
  );
  setSidebarStatus(
    text(payload.globalStatus),
    `${summary.operational || 0} opérationnel(s), ${(summary.degraded || 0) + (summary.unavailable || 0)} incident(s).`,
    payload.globalStatus === "operational" ? "operational" : payload.globalStatus === "degraded" ? "degraded" : "unavailable",
  );
  setPageStatus((payload.alerts || []).length ? `${(payload.alerts || []).length} alerte(s) corrélée(s) active(s).` : "Aucune alerte corrélée en attente.", (payload.alerts || []).length ? "warn" : "ok");

  const rows = (payload.checks || []).map((item) => {
    const tr = document.createElement("tr");
    const service = document.createElement("td");
    service.setAttribute("data-label", "Service");
    service.textContent = text(item.name);
    const liveness = document.createElement("td");
    liveness.setAttribute("data-label", "Liveness");
    liveness.textContent = text(item.liveness);
    const readiness = document.createElement("td");
    readiness.setAttribute("data-label", "Readiness");
    readiness.textContent = text(item.readiness);
    const lastSuccess = document.createElement("td");
    lastSuccess.setAttribute("data-label", "Dernier succès");
    lastSuccess.textContent = formatDate(item.lastSuccessfulCheckAt);
    const message = document.createElement("td");
    message.setAttribute("data-label", "Message");
    message.textContent = text(item.message);
    tr.append(service, liveness, readiness, lastSuccess, message);
    return tr;
  });

  els.contentA.replaceChildren(
    panel("Vérifications", "Séparation liveness/readiness.", [healthTable(rows)]),
  );

  const alertItems = (payload.alerts || []).map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: text(item.service) }),
      Object.assign(document.createElement("div"), { textContent: text(item.message) }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: formatDate(item.date) }),
    );
    return article;
  });
  els.contentB.replaceChildren(
    panel("Alertes corrélées", "Dernières alertes utiles seulement.", [listContainer("healthAlertList")]),
  );
  renderList(document.querySelector("#healthAlertList"), alertItems, "Aucune alerte.");
  els.cardsGrid.replaceChildren();
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === null) continue;
    node.setAttribute(name, String(value));
  }
  return node;
}

function barChart(daily, { keyA, keyB, labelA, labelB, height = 160, width = 420 } = {}) {
  const points = daily.slice(-14);
  if (!points.length) return null;
  const max = Math.max(1, ...points.map((item) => Math.max(item[keyA] || 0, item[keyB] || 0)));
  const pad = 8;
  const gap = 3;
  const groupWidth = width / points.length;
  const barWidth = Math.max(2, groupWidth / 2 - gap);
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart chart-bars",
    role: "img",
    "aria-label": `${labelA} / ${labelB} par jour`,
  });
  points.forEach((item, index) => {
    const x = index * groupWidth + groupWidth / 2;
    const aHeight = ((item[keyA] || 0) / max) * (height - pad);
    const bHeight = ((item[keyB] || 0) / max) * (height - pad);
    const a = svgEl("rect", { x: x - barWidth - gap / 2, y: height - aHeight, width: barWidth, height: Math.max(0, aHeight), rx: 2, class: "bar bar-a" });
    const b = svgEl("rect", { x: x + gap / 2, y: height - bHeight, width: barWidth, height: Math.max(0, bHeight), rx: 2, class: "bar bar-b" });
    const titleA = svgEl("title");
    titleA.textContent = `${item.date} · ${labelA}: ${formatBytes(item[keyA] || 0)}`;
    const titleB = svgEl("title");
    titleB.textContent = `${item.date} · ${labelB}: ${formatBytes(item[keyB] || 0)}`;
    a.append(titleA);
    b.append(titleB);
    svg.append(a, b);
  });
  return svg;
}

function lineChart(daily, { key, label, format = (value) => value, height = 160, width = 420 } = {}) {
  const points = daily.slice(-30);
  if (!points.length) return null;
  const values = points.map((item) => Number(item[key]) || 0);
  const max = Math.max(1, ...values);
  const pad = 10;
  const stepX = width / Math.max(1, points.length - 1);
  const yFor = (value) => height - pad - (value / max) * (height - pad * 2);
  const polyline = values.map((value, index) => `${(index * stepX).toFixed(1)},${yFor(value).toFixed(1)}`);
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart chart-line",
    role: "img",
    "aria-label": label,
  });
  svg.append(svgEl("polyline", { points: polyline.join(" "), class: "chart-line-path" }));
  values.forEach((value, index) => {
    const circle = svgEl("circle", { cx: index * stepX, cy: yFor(value), r: 2.5, class: "chart-line-dot" });
    const title = svgEl("title");
    title.textContent = `${points[index].date} · ${label}: ${format(value)}`;
    circle.append(title);
    svg.append(circle);
  });
  return svg;
}

function legendItem(label, className) {
  return element("span", {
    className: "chart-legend-item",
    children: [element("span", { className: `chart-legend-dot ${className}` }), element("span", { text: label })],
  });
}

function dailyTable(rows) {
  const table = element("table", { className: "data-table stats-table" });
  const headRow = element("tr", {
    children: ["Jour", "Download", "Upload", "Ratio", "Disque", "Actifs", "En erreur", "Médias", "Alertes"].map((label) => element("th", { text: label })),
  });
  const tbody = element("tbody", { children: rows });
  table.append(element("thead", { children: [headRow] }), tbody);
  return element("div", { className: "table-wrap table-scroll", children: [table] });
}

async function renderStats() {
  configureContentGrid("8-4");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Statistiques";
  els.subtitle.textContent = "Historique persistant des volumes échangés, du stockage et de l’activité des torrents.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Métriques";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = await api(`${state.apiPrefix}/stats`, { cache: "no-store" });
  const stats = payload.stats || {};
  const totals = stats.totals || {};
  const daily = stats.daily || [];
  const ratioThreshold = payload.ratioThreshold || {};
  const ratioAlerts = payload.ratioAlerts || [];
  const threshold = ratioThreshold.threshold ?? 10;
  const lastDay = daily.length ? daily[daily.length - 1] : {};

  els.summaryGrid.replaceChildren(
    card("Volume téléchargé", formatBytes(totals.downloaded || 0), `${totals.observedDays || 0} jour(s) observé(s)`),
    card("Volume envoyé", formatBytes(totals.uploaded || 0), totals.ratio ? `Ratio global ${totals.ratio}` : "Aucun volume observé"),
    card("Occupation disque", lastDay.diskUsedPercent != null ? `${lastDay.diskUsedPercent} %` : "—", `${formatBytes(lastDay.diskFreeBytes || 0)} libres`),
    card("Torrents actifs", String(lastDay.activeTorrents ?? 0), `en erreur: ${lastDay.blockedTorrents ?? 0}`),
    card("Médias traités", String(lastDay.mediaCompleted ?? 0), "dernier jour"),
    card("Alertes", String(lastDay.alerts ?? 0), "dernier jour"),
  );
  setSidebarStatus(
    totals.observedDays ? "Historique consolidé" : "Historique en cours d’accumulation",
    `${totals.observedDays || 0} jour(s), ${daily.length} point(s) de mesure.`,
    totals.observedDays ? "operational" : "checking",
  );
  setPageStatus(
    ratioAlerts.length ? `${ratioAlerts.length} torrent(s) au-dessus du seuil de ratio.` : "Aucun torrent au-dessus du seuil de ratio.",
    ratioAlerts.length ? "warn" : "ok",
  );

  const ratioRows = ratioAlerts.map((item) => {
    const article = document.createElement("article");
    article.className = "list-item";
    article.append(
      Object.assign(document.createElement("strong"), { textContent: text(item.name) }),
      Object.assign(document.createElement("div"), { textContent: `Ratio ${item.ratio} · DL ${formatBytes(item.downloaded)} · UL ${formatBytes(item.uploaded)}` }),
      Object.assign(document.createElement("div"), { className: "meta", textContent: text(item.tracker) }),
    );
    return article;
  });

  const thresholdInput = document.createElement("input");
  thresholdInput.type = "number";
  thresholdInput.min = String(ratioThreshold.minThreshold ?? 1);
  thresholdInput.max = String(ratioThreshold.maxThreshold ?? 100);
  thresholdInput.step = String(ratioThreshold.step ?? 0.5);
  thresholdInput.value = String(threshold);
  thresholdInput.className = "input";
  thresholdInput.setAttribute("aria-label", "Seuil de ratio UP/DL");
  const saveThreshold = document.createElement("button");
  saveThreshold.type = "button";
  saveThreshold.className = "button primary";
  saveThreshold.textContent = "Enregistrer";
  saveThreshold.onclick = async () => {
    const value = parseFloat(thresholdInput.value);
    if (Number.isNaN(value)) {
      showToast("Valeur de seuil invalide.");
      return;
    }
    try {
      await api(`${state.apiPrefix}/stats/ratio-threshold`, { method: "POST", body: JSON.stringify({ threshold: value }) });
      showToast("Seuil enregistré.");
      await load();
    } catch (error) {
      showToast(error.message || "Enregistrement impossible.");
    }
  };

  const dailyRows = [...daily].reverse().map((item) => {
    const tr = document.createElement("tr");
    [
      ["Jour", item.date],
      ["Download", formatBytes(item.downloaded || 0)],
      ["Upload", formatBytes(item.uploaded || 0)],
      ["Ratio", String(item.ratio ?? 0)],
      ["Disque", item.diskUsedPercent != null ? `${item.diskUsedPercent} %` : "—"],
      ["Actifs", String(item.activeTorrents ?? 0)],
      ["En erreur", String(item.blockedTorrents ?? 0)],
      ["Médias", String(item.mediaCompleted ?? 0)],
      ["Alertes", String(item.alerts ?? 0)],
    ].forEach(([label, value]) => {
      const td = document.createElement("td");
      td.setAttribute("data-label", label);
      td.textContent = value;
      tr.append(td);
    });
    return tr;
  });

  els.contentA.replaceChildren(
    panel(
      "Évolution",
      "Volumes échangés et occupation disque sur la fenêtre observée.",
      [
        element("div", { className: "chart-legend", children: [legendItem("Download", "download"), legendItem("Upload", "upload")] }),
        element("div", {
          className: "chart-grid",
          children: [
            element("div", { className: "chart-box", children: [barChart(daily, { keyA: "downloaded", keyB: "uploaded", labelA: "Download", labelB: "Upload" }) || systemState({ type: "empty", title: "Aucun point de mesure.", compact: true })] }),
            element("div", { className: "chart-box", children: [lineChart(daily, { key: "diskUsedPercent", label: "Occupation disque", format: (value) => `${value} %` }) || systemState({ type: "empty", title: "Aucun point de mesure.", compact: true })] }),
          ],
        }),
      ],
    ),
    panel(
      "Historique quotidien",
      "Données consolidées et persistées jour par jour.",
      [dailyTable(dailyRows)],
    ),
  );

  els.contentB.replaceChildren(
    panel("Ratio UP/DL", `Seuil actuel: ${threshold}`, [
      element("div", {
        className: "ratio-threshold-form",
        children: [thresholdInput, saveThreshold],
      }),
      element("p", { className: "muted", text: "Les torrents dont le ratio dépasse ce seuil sont signalés." }),
      listContainer("ratioList"),
    ]),
  );
  renderList(document.querySelector("#ratioList"), ratioRows, "Aucun torrent au-dessus du seuil.");
  els.cardsGrid.replaceChildren();
}

async function load() {
  setSidebarStatus("Actualisation en cours", "Synchronisation de la vue.", "checking");
  try {
    if (!state.csrfToken) await refreshSession();
    if (state.section === "activity") await renderActivity();
    if (state.section === "storage") await renderStorage();
    if (state.section === "media") await renderMedia();
    if (state.section === "health") await renderHealth();
    if (state.section === "stats") await renderStats();
    setRefreshStamp();
  } catch (error) {
    setSidebarStatus("Erreur de synchronisation", error.message || "Erreur", "unavailable");
    setPageStatus(error.message || "Erreur de chargement.", "error");
    showToast(error.message || "Erreur");
  }
}

configureLinks();
els.refreshButton?.addEventListener("click", load);
load();
