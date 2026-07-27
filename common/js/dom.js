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

if (typeof window !== "undefined") {
  window.DashboardDOM = Object.freeze({
    element: dashboardElement,
    button: dashboardButton,
  });
}
