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
  loadPromise: null,
  loadQueued: false,
  payloadSignatures: {},
  lastFlows: null,
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
  flowsGrid: document.querySelector("#flowsGrid"),
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

function formatMs(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return `${amount >= 10 ? amount.toFixed(0) : amount.toFixed(1)} ms`;
}

function formatDuration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const remaining = minutes % 60;
  return remaining ? `${hours} h ${remaining} min` : `${hours} h`;
}

function formatClock(value) {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return parsed.toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function modeLabel(mode) {
  return (
    {
      sse: "Temps réel",
      polling: "Secours HTTP",
      paused: "En pause",
      stopped: "Arrêté",
      idle: "Inactif",
    }[mode] || "Inactif"
  );
}

function sourceStatusLabel(value) {
  const key = String(value || "").toLowerCase();
  return (
    {
      connected: "connecté",
      disconnected: "déconnecté",
      firewalled: "pare-feu",
      ok: "ok",
      warning: "avertissement",
      error: "erreur",
      unknown: "inconnu",
    }[key] || text(value)
  );
}

const pageTelemetry = {
  startedAt: Date.now(),
  mode: "idle",
  reconnects: 0,
  frames: {
    total: 0,
    times: [],
    byType: {},
    byTopic: {},
    lastAt: null,
  },
};

if (
  typeof performance !== "undefined" &&
  typeof performance.setResourceTimingBufferSize === "function"
) {
  try {
    performance.setResourceTimingBufferSize(600);
  } catch (error) {
    // Resource timing is optional telemetry.
  }
}

function recordPageFrame(type, frame) {
  const now = Date.now();
  const topic = String(frame?.topic || "system");
  pageTelemetry.frames.total += 1;
  pageTelemetry.frames.times.push(now);
  if (pageTelemetry.frames.times.length > 1200) {
    pageTelemetry.frames.times.splice(0, pageTelemetry.frames.times.length - 1200);
  }
  pageTelemetry.frames.byType[type] = (pageTelemetry.frames.byType[type] || 0) + 1;
  pageTelemetry.frames.byTopic[topic] = (pageTelemetry.frames.byTopic[topic] || 0) + 1;
  pageTelemetry.frames.lastAt = now;
}

function pageFramesPerMinute() {
  const cutoff = Date.now() - 60000;
  const times = pageTelemetry.frames.times;
  let index = 0;
  while (index < times.length && times[index] < cutoff) index += 1;
  return times.length - index;
}

function collectPagePerformance() {
  const result = {
    available: false,
    domContentLoadedMs: null,
    loadMs: null,
    transferBytes: 0,
    decodedBytes: 0,
    resourceCount: 0,
    resourceBytes: 0,
    apiCalls: 0,
    apiBytes: 0,
    apiAverageMs: null,
  };
  if (typeof performance === "undefined" || typeof performance.getEntriesByType !== "function") {
    return result;
  }
  try {
    const navigations = performance.getEntriesByType("navigation") || [];
    const navigation = navigations.length ? navigations[0] : null;
    if (navigation) {
      result.domContentLoadedMs = Math.max(
        0,
        Number(navigation.domContentLoadedEventEnd || 0) - Number(navigation.startTime || 0),
      );
      result.loadMs = Math.max(
        0,
        Number(navigation.loadEventEnd || 0) - Number(navigation.startTime || 0),
      );
      result.transferBytes = Number(navigation.transferSize) || 0;
      result.decodedBytes = Number(navigation.decodedBodySize) || 0;
    }
    const resources = performance.getEntriesByType("resource") || [];
    let apiDuration = 0;
    resources.forEach((entry) => {
      const name = String(entry.name || "");
      const size = Number(entry.transferSize) || 0;
      result.resourceCount += 1;
      result.resourceBytes += size;
      if (
        name.includes(`${state.apiPrefix}/`) &&
        !name.includes("/api/events")
      ) {
        result.apiCalls += 1;
        result.apiBytes += size;
        apiDuration += Number(entry.duration) || 0;
      }
    });
    if (result.apiCalls) result.apiAverageMs = apiDuration / result.apiCalls;
    result.available = true;
  } catch (error) {
    result.available = false;
  }
  return result;
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
  const grid = document.querySelector("#contentA")?.parentElement;
  if (!grid) return;
  grid.className = `content-grid${layout}`;
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
  const desired = items.length ? items : [
    systemState({ type: "empty", title: emptyText, compact: true }),
  ];
  const existing = [...(container.children || [])];
  const existingSignatures = existing.map(nodeRenderSignature);
  const desiredSignatures = desired.map(nodeRenderSignature);
  if (existingSignatures.length === desiredSignatures.length
      && existingSignatures.every((signature, index) => signature === desiredSignatures[index])) {
    return;
  }
  const buckets = new Map();
  existing.forEach((node) => {
    const signature = nodeRenderSignature(node);
    if (!buckets.has(signature)) buckets.set(signature, []);
    buckets.get(signature).push(node);
  });
  const next = desired.map((node, index) => {
    const reusable = buckets.get(desiredSignatures[index]);
    return reusable?.shift() || node;
  });
  container.replaceChildren(...next);
}

function nodeRenderSignature(node) {
  if (node?.outerHTML) return node.outerHTML;
  return String(node?.className || "") + "|" + String(node?.textContent || "") + "|"
    + (node?.children || []).map(nodeRenderSignature).join("|");
}

function payloadAlreadyRendered(section, payload) {
  const signature = JSON.stringify(payload);
  if (state.payloadSignatures[section] === signature) return true;
  state.payloadSignatures[section] = signature;
  return false;
}

async function postJson(path, payload, successMessage, trigger = null) {
  const button = trigger?.tagName === "BUTTON" ? trigger : null;
  const previousText = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = "Traitement…";
  }
  try {
    await api(path, { method: "POST", body: JSON.stringify(payload) });
    showToast(successMessage);
    delete state.payloadSignatures[state.section];
    await load();
  } catch (error) {
    showToast(describeError(error));
    setPageStatus(error.message || "Action impossible.", "error");
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previousText || button.textContent;
    }
  }
}

async function renderActivity(payloadOverride) {
  configureSummaryGrid("summary-grid-trio");
  configureContentGrid("8-4");
  els.title.textContent = "Centre d’activité";
  els.subtitle.textContent = "Synthèse transverse des services, des alertes et des simulations d’automatisation.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Journal";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = payloadOverride || await api(`${state.apiPrefix}/activity`, { cache: "no-store" });
  if (payloadAlreadyRendered("activity", payload)) return;
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
    toggle.onclick = (event) => postJson(
      `${state.apiPrefix}/notifications/${item.status === "acknowledged" ? "reopen" : "ack"}`,
      { code: item.code },
      item.status === "acknowledged" ? "Alerte rouverte." : "Alerte acquittée.",
      event.currentTarget,
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

async function renderStorage(payloadOverride) {
  configureSummaryGrid("summary-grid-storage");
  configureContentGrid("12");
  els.title.textContent = "Panneau de stockage";
  els.subtitle.textContent = "État du montage, statistiques rclone et seuils d’occupation.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Système";
  els.primaryButton.textContent = "Actualiser rclone";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async (event) => {
    await postJson(`${state.apiPrefix}/media-actions/rclone-refresh`, {}, "Actualisation rclone lancée.", event.currentTarget);
  };
  const payload = payloadOverride || await api(`${state.apiPrefix}/storage`, { cache: "no-store" });
  if (payloadAlreadyRendered("storage", payload)) return;
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
    disk.available === false ? "Quota désactivé" : disk.mounted ? "Montage opérationnel" : "Montage indisponible",
    disk.path ? `Chemin surveillé: ${disk.path}` : "Surveillance du stockage active.",
    disk.available === false ? "unavailable" : disk.mounted ? "operational" : "unavailable",
  );
  setPageStatus(
    disk.available === false ? "Le quota Ultra.cc n’est plus interrogé." : disk.mounted ? "Montage et seuils surveillés en continu." : "Le montage doit être vérifié.",
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

async function renderMedia(payloadOverride) {
  configureContentGrid("7-5");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Panneau médias";
  els.subtitle.textContent = "Vue légère Jellyfin: statut, tâches, lectures et derniers médias ajoutés.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Bibliothèque";
  els.primaryButton.textContent = "Scanner Jellyfin";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async (event) => {
    await postJson(`${state.apiPrefix}/media-actions/jellyfin-refresh`, {}, "Scan Jellyfin lancé.", event.currentTarget);
  };
  const payload = payloadOverride || await api(`${state.apiPrefix}/media`, { cache: "no-store" });
  if (payloadAlreadyRendered("media", payload)) return;
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

async function renderHealth(payloadOverride) {
  configureContentGrid("8-4");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Santé du système";
  els.subtitle.textContent = "Liveness, readiness et état global des services exposés via le backend contrôlé.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Observabilité";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = payloadOverride || await api(`${state.apiPrefix}/health/overview`, { cache: "no-store" });
  const summary = payload.summary || {};
  if (payloadAlreadyRendered("health", payload)) return;
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

function flowTile(label, value, hint = "") {
  return element("article", {
    className: "flow-tile",
    children: [
      element("span", { text: label }),
      element("strong", { text: value }),
      hint ? element("small", { text: hint }) : null,
    ].filter(Boolean),
  });
}

function flowKeyValues(rows) {
  return element("dl", {
    className: "flow-kv",
    children: rows.map(([label, value]) =>
      element("div", {
        className: "flow-kv-row",
        children: [
          element("dt", { text: label }),
          element("dd", { text: value }),
        ],
      }),
    ),
  });
}

function flowTable(headers, rows, emptyText = "Aucune donnée sur la fenêtre.") {
  if (!rows.length) return element("p", { className: "muted", text: emptyText });
  const head = element("tr", {
    children: headers.map((label) =>
      element("th", { text: label, attrs: { scope: "col" } }),
    ),
  });
  const body = element("tbody", {
    children: rows.map((cells) =>
      element("tr", {
        children: cells.map((cell, index) =>
          element("td", {
            text: String(cell),
            attrs: { "data-label": headers[index] || "" },
          }),
        ),
      }),
    ),
  });
  return element("div", {
    className: "table-wrap table-scroll",
    children: [
      element("table", {
        className: "data-table flow-table",
        children: [element("thead", { children: [head] }), body],
      }),
    ],
  });
}

function flowSparkline(history) {
  const points = Array.isArray(history) ? history.slice(-60) : [];
  if (points.length < 2) {
    return element("div", {
      className: "chart-box chart-box-empty flow-chart",
      children: [
        element("div", {
          className: "chart-empty",
          children: [
            element("strong", { text: "Débits instantanés" }),
            element("span", { text: "En attente de mesures…" }),
          ],
        }),
      ],
    });
  }
  const series = [
    {
      key: "downloadBytesPerSecond",
      label: "qBittorrent ↓",
      className: "series-a",
      format: (value) => `${formatBytes(value)}/s`,
    },
    {
      key: "uploadBytesPerSecond",
      label: "qBittorrent ↑",
      className: "series-b",
      format: (value) => `${formatBytes(value)}/s`,
    },
    {
      key: "rcloneBytesPerSecond",
      label: "rclone",
      className: "series-c",
      format: (value) => `${formatBytes(value)}/s`,
    },
  ];
  const width = 560;
  const height = 170;
  const padTop = 16;
  const padBottom = 24;
  const padLeft = 48;
  const padRight = 10;
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const stepX = plotWidth / Math.max(1, points.length - 1);
  const xFor = (index) => padLeft + index * stepX;
  const maxValue = niceMax(
    Math.max(
      1,
      ...points.map((item) =>
        Math.max(...series.map((entry) => Number(item[entry.key]) || 0)),
      ),
    ),
  );
  const yFor = (value) => padTop + (1 - (value || 0) / maxValue) * plotHeight;
  const svg = svgEl("svg", {
    viewBox: `0 0 ${width} ${height}`,
    class: "chart chart-lines flow-sparkline",
    role: "img",
    "aria-label": "Débits instantanés qBittorrent et rclone",
  });
  const description = svgEl("desc");
  description.textContent = "Débits instantanés des deux dernières minutes.";
  svg.append(description);
  [0, 0.5, 1].forEach((fraction) => {
    const gridY = yFor(maxValue * fraction);
    svg.append(
      svgEl("line", {
        x1: padLeft,
        x2: width - padRight,
        y1: gridY,
        y2: gridY,
        class: fraction === 0 ? "chart-axis-line" : "chart-grid-line",
      }),
    );
    const label = svgEl("text", {
      x: padLeft - 8,
      y: gridY + 3,
      class: "chart-y-label",
      "text-anchor": "end",
    });
    label.textContent = formatBytes(maxValue * fraction);
    svg.append(label);
  });
  series.forEach((entry) => {
    const className = entry.className ? ` ${entry.className}` : "";
    const xy = points.map((item, index) => ({
      x: xFor(index),
      y: yFor(Number(item[entry.key]) || 0),
    }));
    svg.append(
      svgEl("path", {
        d: smoothPath(xy),
        class: `chart-line-path${className}`,
      }),
    );
    points.forEach((item, index) => {
      const dot = svgEl("circle", {
        cx: xFor(index),
        cy: yFor(Number(item[entry.key]) || 0),
        r: 1.6,
        class: `chart-line-dot${className}`,
      });
      const title = svgEl("title");
      title.textContent = `${formatClock(item.at)} · ${entry.label}: ${entry.format(Number(item[entry.key]) || 0)}`;
      dot.append(title);
      svg.append(dot);
    });
  });
  [0, Math.floor((points.length - 1) / 2), points.length - 1].forEach((index) => {
    const tick = svgEl("text", {
      x: xFor(index),
      y: height - 5,
      class: "chart-tick",
      "text-anchor": index === 0 ? "start" : index === points.length - 1 ? "end" : "middle",
    });
    tick.textContent = formatClock(points[index].at);
    svg.append(tick);
  });
  return element("div", {
    className: "chart-box flow-chart",
    children: [
      element("div", {
        className: "chart-head",
        children: [
          element("strong", { className: "chart-title", text: "Débits instantanés" }),
          element("span", {
            className: "chart-period",
            text: `${points.length} mesures · ${formatClock(points[0].at)} → ${formatClock(points[points.length - 1].at)}`,
          }),
        ],
      }),
      element("div", {
        className: "chart-legend",
        children: series.map((entry) => legendItem(entry.label, entry.className)),
      }),
      svg,
    ],
  });
}

function buildServerFlowsPanel(server) {
  const network = server.network && typeof server.network === "object" ? server.network : {};
  const qbit = server.qbit && typeof server.qbit === "object" ? server.qbit : {};
  const rclone = server.rclone && typeof server.rclone === "object" ? server.rclone : {};
  const totals = server.totals && typeof server.totals === "object" ? server.totals : {};
  const peaks = server.peaks && typeof server.peaks === "object" ? server.peaks : {};
  const history = Array.isArray(server.history) ? server.history : [];
  const networkValue = network.available
    ? `${formatBytes(network.receiveBytesPerSecond || 0)}/s ↓`
    : "Indisponible";
  const networkHint = network.available
    ? `${formatBytes(network.transmitBytesPerSecond || 0)}/s ↑ · ${network.interface || "interface principale"}`
    : "Mesure hôte non exposée";
  const tiles = element("div", {
    className: "flow-tiles",
    children: [
      flowTile(
        "qBittorrent ↓",
        `${formatBytes(qbit.downloadBytesPerSecond || 0)}/s`,
        `pic ${formatBytes(peaks.downloadBytesPerSecond || 0)}/s`,
      ),
      flowTile(
        "qBittorrent ↑",
        `${formatBytes(qbit.uploadBytesPerSecond || 0)}/s`,
        `pic ${formatBytes(peaks.uploadBytesPerSecond || 0)}/s`,
      ),
      flowTile("Réseau hôte", networkValue, networkHint),
      flowTile(
        "rclone",
        `${formatBytes(rclone.speedBytesPerSecond || 0)}/s`,
        `${rclone.transfersActive || 0} transfert(s) · ${rclone.queue || 0} en attente`,
      ),
    ],
  });
  const rows = [
    [
      "Session qBittorrent",
      `${formatBytes(qbit.sessionDownloadedBytes || 0)} ↓ · ${formatBytes(qbit.sessionUploadedBytes || 0)} ↑`,
    ],
    [
      "Volume observé",
      `${formatBytes(totals.downloadedBytes || 0)} ↓ · ${formatBytes(totals.uploadedBytes || 0)} ↑`,
    ],
    ["Volume rclone", formatBytes(rclone.bytesTransferred || 0)],
    ["Ratio global", String(totals.ratio ?? 0)],
    ["Torrents actifs", String(qbit.activeTorrents ?? 0)],
    [
      "Pairs connectés",
      `${qbit.connectedSeeders ?? 0} seeds · ${qbit.connectedLeechers ?? 0} leechers`,
    ],
    [
      "État des sources",
      `qBittorrent ${sourceStatusLabel(qbit.connectionStatus)} · rclone ${sourceStatusLabel(rclone.status)}`,
    ],
    ["Erreurs rclone", String(rclone.errors ?? 0)],
  ];
  const errors = Array.isArray(server.errors) ? server.errors : [];
  const errorLine = errors.length
    ? element("p", {
        className: "flow-errors",
        text: errors.map((item) => text(item?.message, "Source indisponible")).join(" · "),
      })
    : null;
  return panel(
    "Flux serveur",
    "Débits temps réel, volumes échangés et transferts rclone.",
    [tiles, flowSparkline(history), flowKeyValues(rows), errorLine].filter(Boolean),
  );
}

function buildPageFlowsPanel(page) {
  const requests = page.requests && typeof page.requests === "object" ? page.requests : {};
  const live = page.live && typeof page.live === "object" ? page.live : {};
  const performanceInfo = collectPagePerformance();
  const localFramesPerMinute = pageFramesPerMinute();
  const uptimeSeconds = (Date.now() - pageTelemetry.startedAt) / 1000;
  const windowSeconds = Number(page.windowSeconds) || 60;
  const tiles = element("div", {
    className: "flow-tiles",
    children: [
      flowTile(
        "Rafraîchissement",
        modeLabel(pageTelemetry.mode),
        `${live.connectionsActive ?? 0} connexion(s) live`,
      ),
      flowTile(
        "Frames reçues",
        `${localFramesPerMinute}/min`,
        `${pageTelemetry.frames.total} depuis l’ouverture · ${pageTelemetry.reconnects} reconnexion(s)`,
      ),
      flowTile(
        "Requêtes HTTP",
        `${requests.perMinute ?? 0}/min`,
        `${requests.errors ?? 0} erreur(s) · ${formatBytes(requests.bytesOut || 0)} servis (tous onglets)`,
      ),
      flowTile(
        "Latence moyenne",
        formatMs(requests.averageLatencyMs),
        `p95 ${formatMs(requests.p95LatencyMs)}`,
      ),
    ],
  });
  const routeRows = (Array.isArray(requests.routes) ? requests.routes : []).map((item) => [
    `${item.method || "GET"} ${item.route || "—"}`,
    String(item.count ?? 0),
    formatMs(item.averageLatencyMs),
    formatBytes(item.bytesOut || 0),
  ]);
  const topicCounts = live.byTopic && typeof live.byTopic === "object" ? live.byTopic : {};
  const subscribers =
    live.subscribersByTopic && typeof live.subscribersByTopic === "object"
      ? live.subscribersByTopic
      : {};
  const topics = [...new Set([...Object.keys(topicCounts), ...Object.keys(subscribers)])].sort();
  const topicRows = topics.map((topic) => [
    topic,
    String(topicCounts[topic] ?? 0),
    String(subscribers[topic] ?? 0),
  ]);
  const frameTypes = Object.entries(pageTelemetry.frames.byType || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([type, count]) => `${type} ${count}`)
    .join(" · ");
  const rows = [
    [
      "Frames (tous onglets)",
      `${live.framesPerMinute ?? 0}/min · ${live.framesTotal ?? 0} au total`,
    ],
    ["Dernière frame (tous onglets)", formatClock(live.lastFrameAt)],
    ["Octets SSE envoyés", formatBytes(live.bytesTotal || 0)],
    ["Backend", formatDuration(page.processUptimeSeconds)],
    ["Session de cette page", formatDuration(uptimeSeconds)],
    [
      "Chargement initial",
      performanceInfo.available
        ? `DCL ${formatMs(performanceInfo.domContentLoadedMs)} · load ${formatMs(performanceInfo.loadMs)}`
        : "Indisponible",
    ],
    [
      "Poids chargé depuis l’ouverture",
      performanceInfo.available
        ? `${formatBytes(performanceInfo.transferBytes + performanceInfo.resourceBytes)} · ${performanceInfo.resourceCount} ressource(s)`
        : "Indisponible",
    ],
    [
      "API depuis le navigateur (total)",
      performanceInfo.available
        ? `${performanceInfo.apiCalls} appel(s) · ${formatBytes(performanceInfo.apiBytes)} · ${formatMs(performanceInfo.apiAverageMs)}`
        : "Indisponible",
    ],
    ["Types de frames (cet onglet)", frameTypes || "—"],
  ];
  return panel(
    "Flux de la page",
    `Trafic HTTP/SSE sur les ${windowSeconds} dernières secondes, complété par les mesures de cet onglet.`,
    [
      tiles,
      flowKeyValues(rows),
      element("h3", { className: "flow-subheading", text: "Routes principales (tous onglets)" }),
      flowTable(["Route", "Appels", "Latence", "Octets"], routeRows),
      element("h3", { className: "flow-subheading", text: "Frames temps réel par topic (tous onglets)" }),
      flowTable(["Topic", "Frames", "Connexions"], topicRows),
    ],
  );
}

function renderFlows(payload) {
  if (!els.flowsGrid || !payload || typeof payload !== "object") return;
  state.lastFlows = payload;
  const server = payload.server && typeof payload.server === "object" ? payload.server : {};
  const page = payload.page && typeof payload.page === "object" ? payload.page : {};
  els.flowsGrid.replaceChildren(
    buildServerFlowsPanel(server),
    buildPageFlowsPanel(page),
  );
}

function isNewerFlowPayload(candidate, current) {
  if (!current) return true;
  const candidateAt = Date.parse(candidate?.generatedAt || "");
  const currentAt = Date.parse(current?.generatedAt || "");
  if (!Number.isFinite(candidateAt)) return false;
  if (!Number.isFinite(currentAt)) return true;
  return candidateAt >= currentAt;
}

async function renderStats(payloadOverride) {
  configureContentGrid("8-4");
  configureSummaryGrid("summary-grid-trio");
  els.title.textContent = "Statistiques";
  els.subtitle.textContent = "Historique des volumes, flux serveur en direct et trafic de la page.";
  if (els.pageEyebrow) els.pageEyebrow.textContent = "Métriques";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const flowPromise = payloadOverride
    ? Promise.resolve(null)
    : api(`${state.apiPrefix}/data`, { cache: "no-store" }).catch(() => null);
  if (!payloadOverride) {
    flowPromise
      .then((flowPayload) => {
        if (flowPayload && isNewerFlowPayload(flowPayload, state.lastFlows)) {
          renderFlows(flowPayload);
        }
      })
      .catch(() => {});
  }
  const payload = payloadOverride || await api(`${state.apiPrefix}/stats`, { cache: "no-store" });
  const stats = payload.stats || {};
  const totals = stats.totals || {};
  const daily = stats.daily || [];
  const ratioThreshold = payload.ratioThreshold || {};
  const ratioAlerts = payload.ratioAlerts || [];
  const threshold = ratioThreshold.threshold ?? 10;
  const lastDay = daily.length ? daily[daily.length - 1] : {};

  if (payloadAlreadyRendered("stats", payload)) return;
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
    saveThreshold.disabled = true;
    saveThreshold.textContent = "Enregistrement…";
    try {
      await api(`${state.apiPrefix}/stats/ratio-threshold`, { method: "POST", body: JSON.stringify({ threshold: value }) });
      showToast("Seuil enregistré.");
      await load();
    } catch (error) {
      showToast(error.message || "Enregistrement impossible.");
    } finally {
      saveThreshold.disabled = false;
      saveThreshold.textContent = "Enregistrer";
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

function setPageBusy(busy) {
  if (els.refreshButton) els.refreshButton.disabled = busy;
  if (els.primaryButton && ["activity", "health", "stats"].includes(state.section)) {
    els.primaryButton.disabled = busy;
  }
  ["contentA", "contentB", "summaryGrid", "cardsGrid", "flowsGrid"].forEach((key) => {
    els[key]?.setAttribute?.("aria-busy", String(busy));
  });
}

async function load() {
  if (state.loadPromise) {
    state.loadQueued = true;
    return state.loadPromise;
  }
  setPageBusy(true);
  setSidebarStatus("Actualisation en cours", "Synchronisation de la vue.", "checking");
  const request = (async () => {
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
  })();
  state.loadPromise = request.finally(() => {
    state.loadPromise = null;
    setPageBusy(false);
    if (state.loadQueued) {
      state.loadQueued = false;
      window.setTimeout(load, 0);
    }
  });
  return state.loadPromise;
}

function consoleLiveTopic() {
  return ({ activity: "activity", storage: "storage", media: "media", health: "health", stats: "stats" })[state.section];
}

function consoleLiveTopics() {
  const topic = consoleLiveTopic();
  return state.section === "stats" ? [topic, "data"] : [topic];
}

function renderLiveSection(frame) {
  const topic = String(frame?.topic || "");
  const payload = frame?.data;
  if (frame?.stale || (payload && typeof payload === "object" && payload.error)) {
    setSidebarStatus("Données conservées", "La dernière actualisation a échoué; la vue affichée reste disponible.", "degraded");
    setPageStatus("Flux indisponible. Les dernières données valides sont conservées.", "warn");
    return;
  }
  if (frame?.type === "freshness") {
    const collectedAt = frame.sourceUpdatedAt || frame.collectedAt || frame.observedAt;
    if (collectedAt && els.lastCheck) {
      const date = new Date(collectedAt);
      if (!Number.isNaN(date.getTime())) {
        els.lastCheck.textContent = date.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
        els.lastCheck.dateTime = date.toISOString();
      }
    }
    return;
  }
  if (!payload || !consoleLiveTopics().includes(topic)) return;
  if (topic === "data") state.lastFlows = payload;
  const renderers = { activity: renderActivity, storage: renderStorage, media: renderMedia, health: renderHealth, stats: renderStats, data: renderFlows };
  Promise.resolve(renderers[topic]?.(payload)).then(() => {
    const sourceUpdatedAt = frame.sourceUpdatedAt || payload.refreshedAt || payload.generatedAt || payload.observedAt || payload.source_scanned_at;
    if (!sourceUpdatedAt) return;
    const date = new Date(sourceUpdatedAt);
    if (Number.isNaN(date.getTime())) return;
    if (els.lastCheck) {
      els.lastCheck.textContent = date.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
      els.lastCheck.dateTime = date.toISOString();
    }
  }).catch((error) => {
    setPageStatus(error.message || "Erreur de mise à jour.", "error");
  });
}

async function pollConsoleSection({ signal } = {}) {
  if (state.section === "stats") {
    const [stats, data] = await Promise.all([
      api(`${state.apiPrefix}/stats`, { cache: "no-store", retry: false, signal }),
      api(`${state.apiPrefix}/data`, { cache: "no-store", retry: false, signal }).catch(() => null),
    ]);
    return { sections: data ? { stats, data } : { stats } };
  }
  const topic = consoleLiveTopic();
  const paths = { activity: "activity", storage: "storage", media: "media", health: "health/overview", stats: "stats" };
  const data = await api(`${state.apiPrefix}/${paths[topic]}`, { cache: "no-store", retry: false, signal });
  return { sections: { [topic]: data } };
}

function startConsoleLive() {
  if (typeof window.DashboardLive?.createLiveSubscription !== "function") return;
  const topics = consoleLiveTopics();
  const subscription = window.DashboardLive.createLiveSubscription({
    streamUrl: `${state.apiPrefix}/events?topics=${encodeURIComponent(topics.join(","))}`,
    poll: pollConsoleSection,
    pollIntervalMs: 1000,
    reconnectDelayMs: 2500,
    handlers: {
      snapshot(value) {
        recordPageFrame("snapshot", value);
        renderLiveSection(value);
      },
      section(value) {
        recordPageFrame("section", value);
        renderLiveSection(value);
      },
      freshness(value) {
        recordPageFrame("freshness", value);
        renderLiveSection(value);
      },
      job(value) {
        recordPageFrame("job", value);
      },
      heartbeat(value) {
        recordPageFrame("heartbeat", value);
      },
      status(value) {
        const nextMode = value?.mode || "idle";
        if (pageTelemetry.mode === "sse" && nextMode === "polling") {
          pageTelemetry.reconnects += 1;
        }
        pageTelemetry.mode = nextMode;
        if (els.refreshStatus) els.refreshStatus.textContent = value?.mode === "sse" ? "Flux actif" : "Actualisation";
        if (state.section === "stats" && state.lastFlows) renderFlows(state.lastFlows);
      },
      error(error) { setPageStatus(error.message || "Flux interrompu, nouvelle tentative…", "warn"); },
    },
  });
  subscription.start();
}

configureLinks();
els.refreshButton?.addEventListener("click", load);
load().then(startConsoleLive);
