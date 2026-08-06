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
  const diskAvailable = disk.available !== false;
  els.summaryGrid.replaceChildren(
    card("Capacité totale", diskAvailable ? formatBytes(disk.totalBytes || 0) : "Indisponible"),
    card("Utilisé", diskAvailable ? formatBytes(disk.usedBytes || 0) : "Indisponible", diskAvailable ? `${disk.usedPercent || 0} %` : ""),
    card("Disponible", diskAvailable ? formatBytes(disk.freeBytes || 0) : "Indisponible", diskAvailable ? `${disk.freePercent || 0} %` : ""),
    card("Vitesse rclone", rclone.speedLabel || "0 o/s", "", "stat-span-6 stat-compact"),
    card("Erreurs", String(rclone.errors || 0), "", "stat-span-6 stat-compact"),
  );
  setSidebarStatus(
    disk.available === false ? "Quota du slot indisponible" : disk.mounted ? "Montage opérationnel" : "Montage indisponible",
    disk.path ? `Chemin surveillé: ${disk.path}` : "Surveillance du stockage active.",
    disk.available === false ? "unavailable" : disk.mounted ? "operational" : "unavailable",
  );
  setPageStatus(
    disk.available === false ? "Quota du slot indisponible (API quota injoignable)." : disk.mounted ? "Montage et seuils surveillés en continu." : "Le montage doit être vérifié.",
    disk.available === false ? "error" : disk.mounted ? "ok" : "error",
  );

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

let chartUid = 0;

function shortDate(value) {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return String(value || "").slice(5);
  return parsed.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
}

function niceMax(value) {
  const amount = Math.max(Number(value) || 0, 0);
  if (amount <= 0) return 1;
  const exponent = Math.floor(Math.log10(amount));
  const base = 10 ** exponent;
  const norm = amount / base;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
  return nice * base;
}

function smoothPath(xyPoints) {
  if (!xyPoints.length) return "";
  if (xyPoints.length === 1) return `M ${xyPoints[0].x.toFixed(1)},${xyPoints[0].y.toFixed(1)}`;
  if (xyPoints.length === 2) {
    return `M ${xyPoints[0].x.toFixed(1)},${xyPoints[0].y.toFixed(1)} L ${xyPoints[1].x.toFixed(1)},${xyPoints[1].y.toFixed(1)}`;
  }
  let d = `M ${xyPoints[0].x.toFixed(1)},${xyPoints[0].y.toFixed(1)}`;
  for (let i = 0; i < xyPoints.length - 1; i += 1) {
    const p0 = xyPoints[Math.max(0, i - 1)];
    const p1 = xyPoints[i];
    const p2 = xyPoints[i + 1];
    const p3 = xyPoints[Math.min(xyPoints.length - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C ${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  }
  return d;
}

function chartPeriodLabel(points) {
  if (points.length >= 30) return "30 derniers jours";
  const first = points[0].date;
  const last = points[points.length - 1].date;
  return `Du ${shortDate(first)} au ${shortDate(last)}`;
}

function chartEmptyState(title, count) {
  const box = element("div", { className: "chart-box chart-box-empty" });
  const head = element("div", {
    className: "chart-head",
    children: [
      element("strong", { className: "chart-title", text: title }),
      count > 0
        ? element("span", { className: "chart-period", text: `${count} jour${count > 1 ? "s" : ""} observé${count > 1 ? "s" : ""}` })
        : null,
    ].filter(Boolean),
  });
  const body = element("div", {
    className: "chart-empty",
    children: [
      element("strong", { text: "Historique en cours d’accumulation" }),
      element("span", {
        text: count > 0 ? "Un graphique apparaîtra après quelques jours de mesure." : "Aucune donnée enregistrée pour le moment.",
      }),
    ],
  });
  box.append(head, body);
  return box;
}

function seriesLineChart(daily, series, { title = "Évolution", height = 220, width = 480, tickEvery = 5, minPoints = 2 } = {}) {
  const points = daily.slice(-30);
  if (!points.length) return chartEmptyState(title, 0);
  if (points.length < minPoints) return chartEmptyState(title, points.length);
  const padTop = 20;
  const padBottom = 30;
  const padLeft = 54;
  const padRight = 12;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const stepX = plotWidth / Math.max(1, points.length - 1);
  const xFor = (index) => padLeft + index * stepX;
  const maxValue = niceMax(
    Math.max(1, ...points.map((item) => Math.max(...series.map((s) => Number(item[s.key]) || 0)))),
  );
  const yFor = (value) => padTop + (1 - (value || 0) / maxValue) * plotHeight;

  const uid = `chart${chartUid++}`;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart chart-lines",
    role: "img",
    "aria-label": series.map((s) => s.label).join(" / "),
  });
  const description = svgEl("desc");
  description.textContent = `${series.map((s) => s.label).join(" et ")} sur les ${points.length} derniers jours.`;
  svg.append(description);

  const defs = svgEl("defs");
  series.forEach((s, index) => {
    const gradient = svgEl("linearGradient", { id: `${uid}-fill-${index}`, x1: 0, y1: 0, x2: 0, y2: 1 });
    gradient.append(
      svgEl("stop", { offset: "0%", class: `chart-gradient-stop ${s.className}` }),
      svgEl("stop", { offset: "100%", class: `chart-gradient-stop ${s.className}`, "stop-opacity": "0" }),
    );
    defs.append(gradient);
  });
  svg.append(defs);

  const gridValues = [0, 0.25, 0.5, 0.75, 1];
  gridValues.forEach((fraction) => {
    const gridValue = maxValue * fraction;
    const gridY = yFor(gridValue);
    svg.append(svgEl("line", { x1: padLeft, x2: width - padRight, y1: gridY, y2: gridY, class: fraction === 0 ? "chart-axis-line" : "chart-grid-line" }));
    const label = svgEl("text", { x: padLeft - 10, y: gridY + 3, class: "chart-y-label", "text-anchor": "end" });
    label.textContent = series[0].format ? series[0].format(gridValue) : String(Math.round(gridValue));
    svg.append(label);
  });
  const dotsByIndex = Array.from({ length: points.length }, () => []);
  series.forEach((s, index) => {
    const className = s.className ? ` ${s.className}` : "";
    const xy = points.map((item, i) => ({ x: xFor(i), y: yFor(Number(item[s.key]) || 0) }));
    const linePath = smoothPath(xy);
    const baseline = yFor(0);
    const areaPath = `${linePath} L ${xy[xy.length - 1].x.toFixed(1)},${baseline.toFixed(1)} L ${xy[0].x.toFixed(1)},${baseline.toFixed(1)} Z`;
    svg.append(svgEl("path", { d: areaPath, class: `chart-area${className}`, fill: `url(#${uid}-fill-${index})` }));
    svg.append(svgEl("path", { d: linePath, class: `chart-line-path${className}` }));
    points.forEach((item, i) => {
      const dot = svgEl("circle", {
        cx: xFor(i),
        cy: yFor(Number(item[s.key]) || 0),
        r: 1.75,
        class: `chart-line-dot${className}`,
        "data-index": String(i),
      });
      const title = svgEl("title");
      title.textContent = `${item.date} · ${s.label}: ${s.format ? s.format(Number(item[s.key]) || 0) : Number(item[s.key]) || 0}`;
      dot.append(title);
      dotsByIndex[i].push(dot);
      svg.append(dot);
    });
  });

  points.forEach((item, index) => {
    if (index % tickEvery !== 0 && index !== points.length - 1) return;
    const tick = svgEl("text", { x: xFor(index), y: height - 6, class: "chart-tick", "text-anchor": "middle" });
    tick.textContent = shortDate(item.date);
    svg.append(tick);
  });

  const guide = svgEl("line", { y1: padTop, y2: height - padBottom, class: "chart-guide" });
  const hit = svgEl("rect", { x: padLeft, y: padTop, width: plotWidth, height: plotHeight, class: "chart-hit" });
  svg.append(guide, hit);

  const box = element("div", { className: "chart-box", children: [svg] });
  const head = element("div", {
    className: "chart-head",
    children: [
      element("strong", { className: "chart-title", text: title }),
      element("span", { className: "chart-period", text: chartPeriodLabel(points) }),
    ],
  });
  const legend = element("div", {
    className: "chart-legend",
    children: series.map((s) => legendItem(s.label, s.className)),
  });
  const tooltip = element("div", { className: "chart-tooltip", attrs: { role: "tooltip", hidden: "hidden" } });
  box.replaceChildren(head, legend, svg, tooltip);

  const moveAt = (clientX) => {
    const rect = svg.getBoundingClientRect();
    const boxRect = box.getBoundingClientRect();
    if (!rect.width) return;
    const xViewBox = ((clientX - rect.left) / rect.width) * width;
    const index = Math.max(0, Math.min(points.length - 1, Math.round((xViewBox - padLeft) / stepX)));
    const topValue = Math.max(...series.map((s) => Number(points[index][s.key]) || 0));
    const scale = rect.width / width;
    const scaleY = rect.height / height;
    const pointX = rect.left - boxRect.left + xFor(index) * scale;
    const pointY = rect.top - boxRect.top + yFor(topValue) * scaleY;

    dotsByIndex.forEach((group, i) => group.forEach((dot) => dot.classList.toggle("is-active", i === index)));
    guide.setAttribute("x1", xFor(index));
    guide.setAttribute("x2", xFor(index));
    guide.classList.add("is-visible");

    tooltip.replaceChildren(
      Object.assign(document.createElement("strong"), { className: "chart-tooltip-date", textContent: shortDate(points[index].date) }),
      ...series.map((s) => {
        const value = Number(points[index][s.key]) || 0;
        return element("div", {
          className: "chart-tooltip-row",
          children: [
            element("span", { className: `chart-tooltip-dot ${s.className}` }),
            element("span", { text: `${s.label}: ${s.format ? s.format(value) : String(value)}` }),
          ],
        });
      }),
    );
    if (series.length > 1) {
      tooltip.append(
        element("div", {
          className: "chart-tooltip-row",
          children: [
            element("span", { className: "chart-tooltip-dot chart-tooltip-dot-ratio" }),
            element("span", { text: `Ratio: ${points[index].ratio ?? 0}` }),
          ],
        }),
      );
    }
    tooltip.hidden = false;

    const tip = tooltip.getBoundingClientRect();
    let left = pointX - tip.width / 2;
    if (left < 4) left = 4;
    if (left + tip.width > boxRect.width - 4) left = boxRect.width - tip.width - 4;
    const above = pointY - tip.height - 16 >= 4;
    const top = above ? pointY - tip.height - 16 : pointY + 20;
    tooltip.dataset.pos = above ? "above" : "below";
    tooltip.style.setProperty("--arrow-x", `${pointX - left}px`);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${top}px`;
  };

  const hideTooltip = () => {
    guide.classList.remove("is-visible");
    dotsByIndex.forEach((group) => group.forEach((dot) => dot.classList.remove("is-active")));
    tooltip.hidden = true;
  };

  hit.addEventListener("mousemove", (event) => moveAt(event.clientX));
  hit.addEventListener("mouseleave", hideTooltip);
  hit.addEventListener("touchstart", (event) => {
    const touch = event.touches && event.touches[0];
    if (touch) moveAt(touch.clientX);
  }, { passive: true });
  hit.addEventListener("touchend", hideTooltip, { passive: true });

  return box;
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
      "Volumes échangés et occupation disque sur les 30 derniers jours. Survolez la courbe pour afficher les valeurs.",
      [
        element("div", {
          className: "chart-grid",
          children: [
            seriesLineChart(daily, [
              { key: "downloaded", label: "Download", format: formatBytes, className: "series-a" },
              { key: "uploaded", label: "Upload", format: formatBytes, className: "series-b" },
            ], { title: "Volumes échangés" }),
            seriesLineChart(daily, [
              { key: "diskUsedPercent", label: "Occupation disque", format: (value) => `${value} %`, className: "series-a" },
            ], { title: "Occupation disque" }),
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
