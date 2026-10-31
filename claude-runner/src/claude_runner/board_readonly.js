// Read-only mode of the dashboard on the network: the server refuses every write (dashboard_auth.py);
// this script also hides the controls that would write, so the page does not offer what it cannot do.
// Active only when /api/ui-mode says the page is read-only.
"use strict";
const WRITE_LABEL_KEYS = [
  "tr.open", "tr.add", "kan.new", "action.enable", "action.disable", "action.stop", "action.retry",
  "rd.start", "ew.edit", "pe.save", "save.button", "claudemd.save", "btn.group", "btn.cancel",
  "control.button_stop_mission", "prov.add", "prov.edit", "prov.delete", "prov.create",
  "prov.form.save", "prov.key.save", "prov.models.add", "prov.models.save", "prov.models.new",
  "prov.import_title", "prov.fetch.add", "prov.form.template_apply", "lessons.act.edit",
  "lessons.act.delete", "tabs.launch",
];
const WRITE_CONTROL_SELECTOR = "button, [role=button], a.btn";

function writeLabels() {
  const labels = new Set();
  for (const key of WRITE_LABEL_KEYS) {
    const text = String(globalThis.BoardI18n ? globalThis.BoardI18n.t(key) : "").trim();
    if (text && text !== key) labels.add(text.toLowerCase());
  }
  return labels;
}

function hideWriteControls(root) {
  const labels = writeLabels();
  for (const el of root.querySelectorAll(WRITE_CONTROL_SELECTOR)) {
    if (labels.has((el.textContent || "").trim().toLowerCase())) el.hidden = true;
  }
  for (const el of root.querySelectorAll("[draggable=true]")) el.draggable = false;
}

function startReadOnlyMode() {
  let queued = false;
  const run = () => {
    queued = false;
    hideWriteControls(document);
  };
  new MutationObserver(() => {
    if (!queued) { queued = true; requestAnimationFrame(run); }
  }).observe(document.body, { childList: true, subtree: true });
  run();
}

if (typeof document !== "undefined" && typeof fetch === "function") {
  fetch("/api/ui-mode", { credentials: "same-origin" })
    .then((response) => (response.ok ? response.json() : null))
    .then((mode) => {
      if (!mode || !mode.readonly) return;
      document.documentElement.dataset.readonly = "1";
      startReadOnlyMode();
    })
    .catch(() => {});
}
if (typeof module !== "undefined") module.exports = { writeLabels, hideWriteControls };
