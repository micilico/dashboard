function trapFocus(dialog) {
  if (!dialog) return;
  const focusable = dialog.querySelectorAll(
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
  );
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  dialog.addEventListener("keydown", function handler(event) {
    if (event.key !== "Tab") return;
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
    'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
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
