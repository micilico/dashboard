function dashboardElement(tag, options = {}) {
  const node = document.createElement(tag);
  if (options.className) node.className = options.className;
  if (options.text !== undefined) node.textContent = String(options.text);
  if (options.attrs) {
    for (const [name, value] of Object.entries(options.attrs)) {
      if (value !== undefined && value !== null) node.setAttribute(name, String(value));
    }
  }
  if (options.dataset) {
    for (const [name, value] of Object.entries(options.dataset)) {
      node.dataset[name] = String(value);
    }
  }
  if (options.children) node.append(...options.children);
  return node;
}

function dashboardButton(label, className, dataset = {}) {
  return dashboardElement("button", {
    className,
    text: label,
    attrs: { type: "button" },
    dataset,
  });
}

const dashboardStateIcons = {
  loading: '<path d="M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5"></path>',
  empty: '<path d="M4.75 6.75h14.5v10.5H4.75z"></path><path d="M8 10h8M8 14h5"></path>',
  error: '<path d="M12 8v5"></path><path d="M12 17h.01"></path><path d="M10.3 4.7 3.5 17a2 2 0 0 0 1.75 3h13.5a2 2 0 0 0 1.75-3L13.7 4.7a1.95 1.95 0 0 0-3.4 0z"></path>',
  unavailable: '<path d="M12 4.75 18.5 7v5.25c0 4.2-2.6 6.8-6.5 7.99-3.9-1.19-6.5-3.79-6.5-7.99V7z"></path><path d="m9 9 6 6M15 9l-6 6"></path>',
};

function dashboardState({ type = "empty", title = "", message = "", compact = false } = {}) {
  const icon = dashboardElement("span", {
    className: "system-state-icon",
    attrs: { "aria-hidden": "true" },
  });
  icon.innerHTML = `<svg viewBox="0 0 24 24">${dashboardStateIcons[type] || dashboardStateIcons.empty}</svg>`;
  const children = [icon];
  if (title) children.push(dashboardElement("p", { className: "system-state-title", text: title }));
  if (message) children.push(dashboardElement("p", { className: "system-state-message", text: message }));
  return dashboardElement("div", {
    className: `system-state ${type}${compact ? " compact" : ""}`,
    attrs: { role: type === "error" || type === "unavailable" ? "alert" : "status" },
    children,
  });
}

const DASHBOARD_RING_CIRCUMFERENCE = 113.1;

function dashboardFormatBytes(value) {
  const n = Number(value) || 0;
  if (n <= 0) return "0 o";
  const units = ["o", "Ko", "Mo", "Go", "To", "Po"];
  let i = 0;
  let size = n;
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024;
    i += 1;
  }
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function updateDashboardDiskRing({ percent = 0, usedLabel = "", totalLabel = "", usedBytes = 0, totalBytes = 0 } = {}) {
  const ring = document.getElementById("shellDiskRing");
  const fill = document.getElementById("shellDiskFill");
  const text = document.getElementById("shellDiskText");
  const sub = document.getElementById("shellDiskSub");
  if (!ring || !fill) return;
  const pct = Math.min(100, Math.max(0, Number(percent) || 0));
  const used = usedLabel || (usedBytes ? dashboardFormatBytes(usedBytes) : "");
  const total = totalLabel || (totalBytes ? dashboardFormatBytes(totalBytes) : "");
  fill.style.strokeDashoffset = String(DASHBOARD_RING_CIRCUMFERENCE * (1 - pct / 100));
  fill.style.stroke = pct > 90 ? "var(--danger)" : pct > 75 ? "var(--warning)" : "var(--success)";
  if (text) text.textContent = `${pct} %`;
  if (sub) sub.textContent = used && total ? `${used} · ${total}` : "Stockage";
  ring.hidden = false;
}

function hideDashboardDiskRing() {
  const ring = document.getElementById("shellDiskRing");
  if (ring) ring.hidden = true;
}

if (typeof window !== "undefined") {
  window.DashboardDOM = Object.freeze({
    element: dashboardElement,
    button: dashboardButton,
    state: dashboardState,
    updateDiskRing: updateDashboardDiskRing,
    hideDiskRing: hideDashboardDiskRing,
    formatBytes: dashboardFormatBytes,
  });
}
