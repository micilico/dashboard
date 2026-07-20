const consoleConfig = window.__DASHBOARD_CONSOLE_CONFIG__ || {};

const state = {
  section: consoleConfig.section || "activity",
  publicPrefix: consoleConfig.publicPrefix || "",
  apiPrefix: consoleConfig.apiPrefix || "/api",
  torrentPanelPrefix: consoleConfig.torrentPanelPrefix || "/torrent-panel",
  prowlarrPanelPrefix: consoleConfig.prowlarrPanelPrefix || "/prowlarr-panel",
  activityPrefix: consoleConfig.activityPrefix || "/activity",
  storagePrefix: consoleConfig.storagePrefix || "/storage-panel",
  mediaPrefix: consoleConfig.mediaPrefix || "/media-panel",
  healthPrefix: consoleConfig.healthPrefix || "/health",
  csrfToken: "",
};

const els = {
  title: document.querySelector("#pageTitle"),
  subtitle: document.querySelector("#pageSubtitle"),
  refreshStatus: document.querySelector("#refreshStatus"),
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
  storageLink: document.querySelector("#storageLink"),
  mediaLink: document.querySelector("#mediaLink"),
  healthLink: document.querySelector("#healthLink"),
};

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

function text(value, fallback = "—") {
  const cleaned = String(value ?? "").trim();
  return cleaned || fallback;
}

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes <= 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let amount = bytes;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

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

function card(label, value, hint = "") {
  const article = document.createElement("article");
  article.className = "stat panel";
  const top = document.createElement("span");
  top.textContent = label;
  const strong = document.createElement("strong");
  strong.textContent = value;
  article.append(top, strong);
  if (hint) article.append(Object.assign(document.createElement("p"), { className: "muted", textContent: hint }));
  return article;
}

async function api(path, options = {}, retryCsrf = true) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if ((options.method || "GET").toUpperCase() !== "GET" && state.csrfToken) headers.set("X-Torrent-Panel-CSRF", state.csrfToken);
  const response = await fetchWithRetry(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (response.ok) return payload;
  const detail = typeof payload.detail === "object" && payload.detail ? payload.detail : {};
  const error = new Error(detail.message || payload.detail || "Action impossible pour le moment.");
  error.code = detail.code || `http_${response.status}`;
  if (response.status === 403 && error.code === "csrf_expired" && retryCsrf) {
    await refreshSession();
    return api(path, options, false);
  }
  throw error;
}

async function refreshSession() {
  const payload = await api(`${state.apiPrefix}/session`, { cache: "no-store" }, false);
  state.csrfToken = payload.csrfToken || "";
}

function configureLinks() {
  els.homeLink.href = `${state.torrentPanelPrefix}/?view=home`;
  els.activityLink.href = `${state.activityPrefix}/`;
  els.torrentLink.href = `${state.torrentPanelPrefix}/?view=torrents`;
  els.prowlarrLink.href = `${state.prowlarrPanelPrefix}/`;
  els.storageLink.href = `${state.storagePrefix}/`;
  els.mediaLink.href = `${state.mediaPrefix}/`;
  els.healthLink.href = `${state.healthPrefix}/`;
  const map = {
    activity: els.activityLink,
    storage: els.storageLink,
    media: els.mediaLink,
    health: els.healthLink,
  };
  map[state.section]?.setAttribute("aria-current", "page");
}

function renderList(container, items, emptyText) {
  container.replaceChildren(...(items.length ? items : [Object.assign(document.createElement("div"), { className: "empty", textContent: emptyText })]));
}

async function postJson(path, payload, successMessage) {
  await api(path, { method: "POST", body: JSON.stringify(payload) });
  showToast(successMessage);
  await load();
}

async function renderActivity() {
  els.title.textContent = "Centre d’activité";
  els.subtitle.textContent = "Synthèse transverse des services, des alertes et des simulations d’automatisation.";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = await api(`${state.apiPrefix}/activity`, { cache: "no-store" });
  const summary = payload.summary || {};
  els.summaryGrid.replaceChildren(
    card("Téléchargements actifs", String(summary.downloadsActive || 0)),
    card("Vitesse descendante", `${formatBytes(summary.downloadSpeedBytes || 0)}/s`),
    card("Vitesse montante", `${formatBytes(summary.uploadSpeedBytes || 0)}/s`),
    card("Torrents bloqués", String(summary.blockedTorrents || 0)),
    card("Espace libre", formatBytes(summary.diskFreeBytes || 0)),
  );

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
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Chronologie récente</h2><p class="muted">Événements consolidés sans secret.</p></div></div><div class="panel-body"><div id="timelineList" class="timeline"></div></div>`,
    }),
  );
  renderList(document.querySelector("#timelineList"), timelineItems, "Aucun événement récent.");

  els.contentB.replaceChildren(
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Notifications</h2><p class="muted">Déduplication, acquittement et réouverture.</p></div></div><div class="panel-body"><div id="notificationList" class="list"></div></div>`,
    }),
  );
  renderList(document.querySelector("#notificationList"), alertItems, "Aucune alerte active.");

  els.cardsGrid.replaceChildren(...simulationCards);
}

async function renderStorage() {
  els.title.textContent = "Panneau de stockage";
  els.subtitle.textContent = "État du montage, statistiques rclone et seuils d’occupation.";
  els.primaryButton.textContent = "Actualiser rclone";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async () => {
    await postJson(`${state.apiPrefix}/media-actions/rclone-refresh`, {}, "Actualisation rclone lancée.");
  };
  const payload = await api(`${state.apiPrefix}/storage`, { cache: "no-store" });
  const disk = payload.disk || {};
  const rclone = payload.rclone || {};
  els.summaryGrid.replaceChildren(
    card("Capacité totale", formatBytes(disk.totalBytes || 0)),
    card("Utilisé", formatBytes(disk.usedBytes || 0), `${disk.usedPercent || 0} %`),
    card("Disponible", formatBytes(disk.freeBytes || 0), `${disk.freePercent || 0} %`),
    card("Vitesse rclone", rclone.speedLabel || "0 o/s"),
    card("Erreurs", String(rclone.errors || 0)),
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
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Montage et seuils</h2><p class="muted">Chemin surveillé: ${text(disk.path)}</p></div>${badge(disk.mounted ? "Monté" : "Indisponible", disk.mounted ? "ok" : "error").outerHTML}</div><div class="panel-body"><p class="muted">Statut: ${text(disk.status)}.</p></div>`,
    }),
  );

  els.contentB.replaceChildren(
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Transferts actifs</h2><p class="muted">Dernière réponse réussie: ${formatDate(rclone.lastSuccessfulResponseAt)}</p></div></div><div class="panel-body"><div id="transferList" class="list"></div></div>`,
    }),
  );
  renderList(document.querySelector("#transferList"), transfers, rclone.errorMessage || "Aucun transfert actif.");
  els.cardsGrid.replaceChildren();
}

async function renderMedia() {
  els.title.textContent = "Panneau médias";
  els.subtitle.textContent = "Vue légère Jellyfin: statut, tâches, lectures et derniers médias ajoutés.";
  els.primaryButton.textContent = "Scanner Jellyfin";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = async () => {
    await postJson(`${state.apiPrefix}/media-actions/jellyfin-refresh`, {}, "Scan Jellyfin lancé.");
  };
  const payload = await api(`${state.apiPrefix}/media`, { cache: "no-store" });
  els.summaryGrid.replaceChildren(
    card("Serveur", text(payload.serverName)),
    card("Version", text(payload.version)),
    card("Lectures en cours", String((payload.sessions || []).length)),
    card("Utilisateurs", String((payload.activeUsers || []).length)),
    card("Tâches", String((payload.tasks || []).length)),
  );

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
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Derniers médias ajoutés</h2><p class="muted">Ouverture native via Jellyfin disponible.</p></div></div><div class="panel-body"><div id="recentMediaList" class="list"></div></div>`,
    }),
  );
  renderList(document.querySelector("#recentMediaList"), recentItems, (payload.errors || []).join(" ") || "Aucun média récent.");

  els.contentB.replaceChildren(
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Tâches planifiées</h2><p class="muted">Tâches et scans observables.</p></div></div><div class="panel-body"><div id="taskList" class="list"></div></div>`,
    }),
  );
  renderList(document.querySelector("#taskList"), tasks, "Aucune tâche visible.");
  els.cardsGrid.replaceChildren();
}

async function renderHealth() {
  els.title.textContent = "Santé du système";
  els.subtitle.textContent = "Liveness, readiness et état global des services exposés via le backend contrôlé.";
  els.primaryButton.textContent = "Actualiser";
  els.primaryButton.hidden = false;
  els.primaryButton.onclick = load;
  const payload = await api(`${state.apiPrefix}/health/overview`, { cache: "no-store" });
  const summary = payload.summary || {};
  els.summaryGrid.replaceChildren(
    card("État global", text(payload.globalStatus)),
    card("Opérationnels", String(summary.operational || 0)),
    card("Dégradés", String(summary.degraded || 0)),
    card("Indisponibles", String(summary.unavailable || 0)),
    card("Alertes", String((payload.alerts || []).length)),
  );

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
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Vérifications</h2><p class="muted">Séparation liveness/readiness.</p></div></div><div class="panel-body"><div class="table-wrap"><table><thead><tr><th>Service</th><th>Liveness</th><th>Readiness</th><th>Dernier succès</th><th>Message</th></tr></thead><tbody id="healthRows"></tbody></table></div></div>`,
    }),
  );
  document.querySelector("#healthRows").replaceChildren(...rows);

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
    Object.assign(document.createElement("section"), {
      className: "panel",
      innerHTML: `<div class="panel-head"><div><h2>Alertes corrélées</h2><p class="muted">Dernières alertes utiles seulement.</p></div></div><div class="panel-body"><div id="healthAlertList" class="list"></div></div>`,
    }),
  );
  renderList(document.querySelector("#healthAlertList"), alertItems, "Aucune alerte.");
  els.cardsGrid.replaceChildren();
}

async function load() {
  els.refreshStatus.textContent = "Actualisation…";
  try {
    if (!state.csrfToken) await refreshSession();
    if (state.section === "activity") await renderActivity();
    if (state.section === "storage") await renderStorage();
    if (state.section === "media") await renderMedia();
    if (state.section === "health") await renderHealth();
    els.refreshStatus.textContent = `À jour ${new Date().toLocaleTimeString("fr-FR")}`;
  } catch (error) {
    els.refreshStatus.textContent = "Erreur";
    showToast(error.message || "Erreur");
  }
}

configureLinks();
els.refreshButton?.addEventListener("click", load);
load();
