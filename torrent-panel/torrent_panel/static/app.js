const state = {
  csrfToken: "",
  torrents: [],
  pendingDelete: null,
  refreshTimer: null,
};

const els = {
  rows: document.querySelector("#torrentRows"),
  summary: document.querySelector("#summary"),
  alert: document.querySelector("#alert"),
  empty: document.querySelector("#emptyState"),
  refreshStatus: document.querySelector("#refreshStatus"),
  refreshButton: document.querySelector("#refreshButton"),
  addForm: document.querySelector("#addForm"),
  magnetInput: document.querySelector("#magnetInput"),
  magnetMessage: document.querySelector("#magnetMessage"),
  toast: document.querySelector("#toast"),
  deleteDialog: document.querySelector("#deleteDialog"),
  deleteForm: document.querySelector("#deleteForm"),
  deleteTorrentName: document.querySelector("#deleteTorrentName"),
  strongConfirm: document.querySelector("#strongConfirm"),
  confirmText: document.querySelector("#confirmText"),
  confirmDeleteButton: document.querySelector("#confirmDeleteButton"),
};

function formatBytes(value) {
  const bytes = Number(value) || 0;
  if (bytes === 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function formatSpeed(value) {
  return `${formatBytes(value)}/s`;
}

function formatRatio(value) {
  return (Number(value) || 0).toFixed(2);
}

function stateLabel(rawState) {
  const value = String(rawState || "inconnu");
  const paused = /paused|stopped/i.test(value);
  const active = /downloading|uploading|stalled|checking|queued/i.test(value) && !paused;
  return { text: value, paused, active };
}

function showToast(message) {
  els.toast.textContent = message;
  els.toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    els.toast.hidden = true;
  }, 4200);
}

function showError(message) {
  els.alert.textContent = message;
  els.alert.hidden = false;
}

function clearError() {
  els.alert.hidden = true;
  els.alert.textContent = "";
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("Accept", "application/json");
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if ((options.method || "GET").toUpperCase() !== "GET") {
    headers.set("X-Torrent-Panel-CSRF", state.csrfToken);
  }

  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Action impossible pour le moment.");
  }
  return payload;
}

function render() {
  els.rows.innerHTML = "";
  els.empty.hidden = state.torrents.length !== 0;
  els.summary.textContent = `${state.torrents.length} torrent${state.torrents.length > 1 ? "s" : ""}`;

  for (const torrent of state.torrents) {
    const status = stateLabel(torrent.state);
    const progress = Math.round((Number(torrent.progress) || 0) * 1000) / 10;
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="Nom"><div class="torrent-name"></div></td>
      <td data-label="Etat"><span class="badge ${status.paused ? "paused" : status.active ? "active" : ""}"></span></td>
      <td data-label="Progression" class="progress-cell">
        <div class="progress-track" aria-hidden="true"><div class="progress-bar" style="width: ${progress}%"></div></div>
        <div class="progress-text">${progress.toFixed(1)}%</div>
      </td>
      <td data-label="Telechargement" class="mono">${formatSpeed(torrent.downloadSpeed)}</td>
      <td data-label="Envoi" class="mono">${formatSpeed(torrent.uploadSpeed)}</td>
      <td data-label="Ratio" class="mono">${formatRatio(torrent.ratio)}</td>
      <td data-label="Taille" class="mono">${formatBytes(torrent.size)}</td>
      <td data-label="Actions"><div class="action-row"></div></td>
    `;
    tr.querySelector(".torrent-name").textContent = torrent.name;
    tr.querySelector(".badge").textContent = status.text;
    const actions = tr.querySelector(".action-row");
    const pauseButton = document.createElement("button");
    pauseButton.type = "button";
    pauseButton.className = `button ${status.paused ? "primary" : "secondary"}`;
    pauseButton.textContent = status.paused ? "Reprendre" : "Pause";
    pauseButton.addEventListener("click", () => changeTorrentState(torrent, status.paused ? "resume" : "pause"));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "button danger";
    deleteButton.textContent = "Supprimer";
    deleteButton.addEventListener("click", () => openDeleteDialog(torrent));

    actions.append(pauseButton, deleteButton);
    els.rows.append(tr);
  }
}

async function loadTorrents(silent = false) {
  if (!silent) {
    els.refreshStatus.textContent = "Actualisation...";
  }
  try {
    const payload = await api("api/torrents");
    state.torrents = payload.torrents || [];
    render();
    clearError();
    els.refreshStatus.textContent = `A jour ${new Date().toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
  } catch (error) {
    showError(error.message);
    els.refreshStatus.textContent = "Erreur";
  }
}

async function changeTorrentState(torrent, action) {
  try {
    await api(`api/torrents/${action}`, {
      method: "POST",
      body: JSON.stringify({ hash: torrent.hash }),
    });
    showToast(action === "pause" ? "Torrent mis en pause." : "Torrent repris.");
    await loadTorrents(true);
  } catch (error) {
    showError(error.message);
  }
}

function openDeleteDialog(torrent) {
  state.pendingDelete = torrent;
  els.deleteTorrentName.textContent = torrent.name;
  els.deleteForm.deleteMode.value = "torrent";
  els.confirmText.value = "";
  updateDeleteConfirm();
  els.deleteDialog.showModal();
}

function updateDeleteConfirm() {
  const withFiles = els.deleteForm.deleteMode.value === "files";
  els.strongConfirm.hidden = !withFiles;
  els.confirmDeleteButton.disabled = withFiles && els.confirmText.value.trim() !== "SUPPRIMER";
}

async function confirmDelete() {
  if (!state.pendingDelete) return;
  const deleteFiles = els.deleteForm.deleteMode.value === "files";
  try {
    await api("api/torrents/delete", {
      method: "POST",
      body: JSON.stringify({ hash: state.pendingDelete.hash, deleteFiles }),
    });
    showToast(deleteFiles ? "Torrent et fichiers supprimes." : "Torrent supprime.");
    state.pendingDelete = null;
    await loadTorrents(true);
  } catch (error) {
    showError(error.message);
  }
}

els.refreshButton.addEventListener("click", () => loadTorrents());

els.addForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  els.magnetMessage.textContent = "";
  const magnet = els.magnetInput.value.trim();
  try {
    await api("api/torrents/add", {
      method: "POST",
      body: JSON.stringify({ magnet }),
    });
    els.magnetInput.value = "";
    els.magnetMessage.textContent = "Magnet ajoute.";
    showToast("Torrent envoye a qBittorrent.");
    await loadTorrents(true);
  } catch (error) {
    els.magnetMessage.textContent = error.message;
    els.magnetInput.focus();
  }
});

els.deleteForm.addEventListener("change", updateDeleteConfirm);
els.confirmText.addEventListener("input", updateDeleteConfirm);
els.deleteForm.addEventListener("submit", (event) => {
  if (event.submitter?.value !== "confirm") return;
  event.preventDefault();
  els.deleteDialog.close();
  confirmDelete();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) loadTorrents(true);
});

async function init() {
  try {
    const session = await api("api/session");
    state.csrfToken = session.csrfToken;
    await loadTorrents();
    state.refreshTimer = window.setInterval(() => {
      if (!document.hidden) loadTorrents(true);
    }, 6000);
  } catch (error) {
    showError(error.message);
    els.refreshStatus.textContent = "Erreur";
  }
}

init();
