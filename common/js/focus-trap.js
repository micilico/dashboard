function trapFocus(dialog) {
  if (!dialog || dialog.dataset.focusTrapReady === "true") return;
  dialog.dataset.focusTrapReady = "true";
  dialog.addEventListener("keydown", function handler(event) {
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll(
      'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
    )].filter(node => !node.hidden);
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) return;
    if (event.shiftKey) {
      if (document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      }
    } else {
      if (document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    }
  });
}

function openDialog(dialog, trigger) {
  if (!dialog) return;
  dialog._lastFocus = trigger || document.activeElement;
  trapFocus(dialog);
  dialog.showModal();
  const first = dialog.querySelector(
    '[autofocus], button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
  );
  first?.focus();
  dialog.addEventListener("close", function restore() {
    dialog._lastFocus?.focus?.();
    dialog.removeEventListener("close", restore);
  }, { once: true });
}

if (typeof window !== "undefined") {
  window.trapFocus = trapFocus;
  window.openDialog = openDialog;
}
