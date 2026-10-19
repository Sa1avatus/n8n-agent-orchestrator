/* board-scope-wrapped */
(function () {
/* The launch settings editor as a window with steps (docs/board-mission-edit-mockup.html).
 *
 * board_mission.js renders the launch form (.confbox with one .frow-wrap row per
 * field) and board_mission_save.js reads, diffs and saves it through the
 * data-key controls.  This module does not touch that data path: it moves the
 * form rows into the pages of a window (the controls stay inside #boardmission,
 * so readForm, the dirty check and doSave work as before) and leaves a summary
 * card with an "Edit launch settings" button where the form was.
 *
 *   page 1  Mission    name, objective, rules, acceptance criteria, working folder
 *   page 2  Execution  attempts per task, roles (model + provider on one line), limits, thinking
 *   page 3  Context    hints, required files, retrieval corpora
 *   page 4  Review     the diff (BoardMissionSave.currentDiff) and "Save changes"
 *
 * #boardmission is re-rendered after a save and on refresh; a MutationObserver
 * builds the window again on the fresh form.  While the window is open the
 * auto-refresh is paused (body.dataset.editing).
 */
"use strict";

const PAGES = [
  { id: "mission", label: "ew.s1", keys: ["name", "objective", "rules", "acceptance_criteria", "working_directory"] },
  { id: "exec", label: "ew.s2", keys: ["attempts_per_task", "enabled", "worker_model", "worker_provider", "reviewer_model",
    "reviewer_provider", "architect_model", "architect_provider", "max_tasks", "worker_think_policy_from_task", "worker_think_policy"] },
  { id: "ctx", label: "ew.s3", keys: ["hints", "required_files", "retrieval_corpora"] },
];
const ROLES = [
  ["ew.worker", "#ff8a3d", "worker_model", "worker_provider"],
  ["ew.reviewer", "#b39bff", "reviewer_model", "reviewer_provider"],
  ["ew.architect", "#ffb454", "architect_model", "architect_provider"],
];
const LOCKED = ["working_directory", "retrieval_corpora"];

function tr(key, params) {
  return typeof t === "function" ? t(key, params) : key;
}

function mk(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function save() {
  return typeof BoardMissionSave !== "undefined" ? BoardMissionSave : null;
}

function control(box, key) {
  return box.querySelector('[data-key="' + key + '"]');
}

function rowOf(box, key) {
  const c = control(box, key);
  return c ? c.closest(".frow-wrap") : null;
}

function labelOf(box, key) {
  const row = rowOf(box, key);
  const l = row && row.querySelector(".frow-label");
  return l ? l.textContent : key;
}

function show(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// --- the summary card that replaces the inline form -------------------------------------

function summaryCard(box) {
  const card = mk("div", "ew-summary");
  const cur = save() ? save().readForm(box) : {};
  // an empty field uses the default of the hermes_config profile: it is the placeholder of the control
  const eff = (key) => {
    const v = cur[key];
    if (v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length)) return show(v);
    const c = control(box, key);
    return c && c.placeholder ? c.placeholder : "—";
  };
  const items = [
    [tr("ew.sec.attempts"), eff("attempts_per_task")],
    [tr("ew.worker"), eff("worker_model") + " · " + eff("worker_provider")],
    [tr("ew.reviewer"), eff("reviewer_model") + " · " + eff("reviewer_provider")],
    [tr("ew.architect"), eff("architect_model") + " · " + eff("architect_provider")],
    [labelOf(box, "working_directory"), show(cur.working_directory)],
    [labelOf(box, "enabled"), cur.enabled ? tr("form.yes") : tr("form.no")],
  ];
  const grid = mk("div", "ew-sumgrid");
  for (const [k, v] of items) {
    const cell = mk("div", "ew-sum");
    cell.append(mk("span", "", k), mk("b", "", v));
    grid.appendChild(cell);
  }
  const actions = mk("div", "ew-actions");
  const edit = mk("button", "ew-pri", "✎ " + tr("ew.edit"));
  edit.type = "button";
  edit.addEventListener("click", () => openWin(box));
  const copy = mk("button", "", tr("save.copy_from"));
  copy.type = "button";
  copy.addEventListener("click", () => { if (save()) save().onCopy(box); });
  actions.append(edit, copy);
  card.append(mk("h3", "", tr("form.title")), grid, actions);
  return card;
}

// --- the window -------------------------------------------------------------------------

let step = 0;
let maximised = false;

function pageNode(id) {
  return document.getElementById("ew-page-" + id);
}

function fillPages(box, win) {
  const lockedNote = (key) => {
    const row = rowOf(box, key);
    const cur = control(box, key);
    const box2 = mk("div", "ew-lock");
    box2.append(mk("span", "", "🔒"), mk("span", "mono", cur ? cur.value.split("\n").join(", ") : ""),
      mk("small", "", labelOf(box, key) + " · " + tr("ew.locked")));
    if (row) row.hidden = true;
    return box2;
  };
  const sec = (title, hint) => {
    const s = mk("div", "ew-sec");
    s.appendChild(mk("h4", "", title));
    if (hint) s.appendChild(mk("p", "", hint));
    return s;
  };
  const move = (parent, key) => {
    const row = rowOf(box, key);
    if (row && !row.hidden) parent.appendChild(row);
  };
  const running = box.querySelector(".meta.running-note");

  // page 1
  const p1 = pageNode("mission");
  if (running) p1.appendChild(mk("div", "ew-warn", running.textContent));
  const s1 = sec(tr("ew.sec.mission"), tr("ew.hint.mission"));
  move(s1, "name"); move(s1, "objective");
  const s2 = sec(tr("ew.sec.rules"), tr("ew.hint.rules"));
  const two = mk("div", "ew-row2");
  move(two, "rules"); move(two, "acceptance_criteria");
  s2.appendChild(two);
  const s3 = sec(tr("ew.sec.where"));
  s3.appendChild(lockedNote("working_directory"));
  p1.append(s1, s2, s3);

  // page 2
  const p2 = pageNode("exec");
  const a = sec(tr("ew.sec.attempts"), tr("ew.hint.attempts"));
  move(a, "attempts_per_task");
  const r = sec(tr("ew.sec.roles"), tr("ew.hint.roles") + " " + tr("ew.hint.default"));
  const roles = mk("div", "ew-roles");
  for (const [labelKey, color, km, kp] of ROLES) {
    const line = mk("div", "ew-role");
    const name = mk("b", "");
    const dot = mk("span", "ew-dot");
    dot.style.background = color;
    name.append(dot, document.createTextNode(tr(labelKey)));
    line.appendChild(name);
    for (const [key, cap] of [[km, tr("ew.model")], [kp, tr("ew.provider")]]) {
      const cell = mk("div", "ew-cell");
      cell.appendChild(mk("small", "", cap));
      const c = control(box, key);
      const row = rowOf(box, key);
      if (c) cell.appendChild(c);
      if (row) { row.hidden = true; }
      line.appendChild(cell);
    }
    roles.appendChild(line);
  }
  r.appendChild(roles);
  const l = sec(tr("ew.sec.limits"));
  const two2 = mk("div", "ew-row2");
  move(two2, "max_tasks"); move(two2, "worker_think_policy_from_task");
  l.appendChild(two2);
  move(l, "enabled");
  const th = sec(tr("ew.sec.policy"), tr("ew.hint.policy"));
  move(th, "worker_think_policy");
  p2.append(a, r, l, th);

  // page 3
  const p3 = pageNode("ctx");
  const h = sec(tr("ew.sec.hints"), tr("ew.hint.hints"));
  move(h, "hints"); move(h, "required_files");
  const c3 = sec(labelOf(box, "retrieval_corpora"));
  c3.appendChild(lockedNote("retrieval_corpora"));
  p3.append(h, c3);
}

function reviewHtml(box) {
  const page = pageNode("review");
  page.textContent = "";
  const s = save();
  const diff = s ? s.currentDiff(box) : [];
  const sec = mk("div", "ew-sec");
  sec.appendChild(mk("h4", "", tr("ew.s4")));
  sec.appendChild(mk("p", "", diff.length ? tr("ew.changes", { n: diff.length }) : tr("ew.nochange")));
  if (diff.length) {
    const table = mk("table", "ew-diff");
    const head = mk("tr", "");
    for (const th of [tr("ew.field"), tr("ew.before"), tr("ew.after")]) head.appendChild(mk("th", "", th));
    table.appendChild(head);
    for (const d of diff) {
      const row = mk("tr", "");
      row.append(mk("td", "ew-field", labelOf(box, d.field)),
        mk("td", "ew-old", show(d.old).slice(0, 160)), mk("td", "ew-new", show(d.new).slice(0, 160)));
      table.appendChild(row);
    }
    sec.appendChild(table);
  }
  page.appendChild(sec);
  if (box.querySelector(".meta.running-note") && diff.length) {
    page.appendChild(mk("div", "ew-warn", tr("ew.midrun")));
  }
}

function problems(box) {
  const cur = save() ? save().readForm(box) : {};
  if (!String(cur.name || "").trim() || !String(cur.objective || "").trim()) return { page: 0, text: tr("ew.invalid") };
  if (typeof cur.worker_think_policy === "string" && cur.worker_think_policy.trim() !== "") return { page: 1, text: tr("ew.badjson") };
  return null;
}

function paint(box) {
  const win = document.getElementById("ew-win");
  if (!win) return;
  const total = PAGES.length + 1;
  const dirty = save() ? save().formDirty(box) : false;
  document.getElementById("ew-bar").style.width = ((step + 1) / total * 100) + "%";
  const labels = PAGES.map((p) => tr(p.label)).concat([tr("ew.s4")]);
  const steps = document.getElementById("ew-steps");
  steps.textContent = "";
  labels.forEach((text, i) => {
    const b = mk("button", "ew-st" + (i === step ? " on" : i < step ? " done" : ""));
    b.type = "button";
    b.append(mk("i", "", i < step ? "✓" : String(i + 1)), document.createTextNode(text));
    b.addEventListener("click", () => { step = i; paint(box); });
    steps.appendChild(b);
  });
  for (const p of PAGES) pageNode(p.id).hidden = PAGES[step] !== p;
  pageNode("review").hidden = step !== PAGES.length;
  if (step === PAGES.length) reviewHtml(box);
  const last = step === PAGES.length;
  document.getElementById("ew-back").disabled = step === 0;
  const next = document.getElementById("ew-next");
  next.textContent = last ? tr("ew.save") : tr("ew.next") + " ›";
  next.disabled = last && !dirty;
  document.getElementById("ew-hint").textContent = labels[step] + " · " + tr("ew.step", { a: step + 1, b: total });
  document.getElementById("ew-dirty").classList.toggle("on", dirty);
  document.getElementById("ew-err").textContent = "";
  document.getElementById("ew-body").scrollTop = 0;
}

function closeWin(box, force) {
  const s = save();
  if (!force && s && s.formDirty(box) && !window.confirm(tr("ew.discard"))) return;
  if (!force && s && s.formDirty(box)) s.onReset(box);
  const scrim = document.getElementById("ew-scrim");
  if (scrim) scrim.classList.remove("open");
  document.body.dataset.editing = "";
  document.removeEventListener("keydown", onKey);
  const old = box.querySelector(".ew-summary");
  if (old) old.replaceWith(summaryCard(box));
}

function onKey(event) {
  const box = document.getElementById("boardmission");
  if (event.key === "Escape" && box) closeWin(box, false);
}

async function onNext(box) {
  if (step < PAGES.length) { step += 1; paint(box); return; }
  const bad = problems(box);
  if (bad) { step = bad.page; paint(box); document.getElementById("ew-err").textContent = bad.text; return; }
  const next = document.getElementById("ew-next");
  next.disabled = true;
  try {
    await save().doSave(box);          // re-renders #boardmission: the observer builds the card again
  } finally {
    document.body.dataset.editing = "";
  }
}

function openWin(box) {
  const scrim = document.getElementById("ew-scrim");
  if (!scrim) return;
  step = 0;
  scrim.classList.add("open");
  document.body.dataset.editing = "1";
  document.addEventListener("keydown", onKey);
  paint(box);
}

function buildWindow(box) {
  const scrim = mk("div", "ew-scrim");
  scrim.id = "ew-scrim";
  const win = mk("div", "ew-win");
  win.id = "ew-win";
  win.setAttribute("role", "dialog");
  win.setAttribute("aria-modal", "true");
  const head = mk("div", "ew-head");
  const title = mk("h3", "", tr("ew.title"));
  const dirty = mk("span", "ew-dirty", tr("ew.unsaved"));
  dirty.id = "ew-dirty";
  const maxBtn = mk("button", "ew-ib", "⤢");
  maxBtn.type = "button";
  maxBtn.title = tr("ew.max");
  maxBtn.addEventListener("click", () => {
    maximised = !maximised;
    win.classList.toggle("max", maximised);
    maxBtn.textContent = maximised ? "⤡" : "⤢";
  });
  const closeBtn = mk("button", "ew-ib", "✕");
  closeBtn.type = "button";
  closeBtn.title = tr("ew.close");
  closeBtn.addEventListener("click", () => closeWin(box, false));
  head.append(title, dirty, mk("span", "ew-sp"), maxBtn, closeBtn);
  const prog = mk("div", "ew-prog");
  const bar = mk("i", "");
  bar.id = "ew-bar";
  prog.appendChild(bar);
  const steps = mk("div", "ew-steps");
  steps.id = "ew-steps";
  const body = mk("div", "ew-body");
  body.id = "ew-body";
  for (const p of PAGES.concat([{ id: "review" }])) {
    const page = mk("div", "ew-page");
    page.id = "ew-page-" + p.id;
    body.appendChild(page);
  }
  const foot = mk("div", "ew-foot");
  const back = mk("button", "", "‹ " + tr("ew.back"));
  back.id = "ew-back";
  back.type = "button";
  back.addEventListener("click", () => { step = Math.max(0, step - 1); paint(box); });
  const hint = mk("span", "ew-hintline");
  hint.id = "ew-hint";
  const err = mk("span", "ew-err");
  err.id = "ew-err";
  const cancel = mk("button", "", tr("ew.cancel"));
  cancel.type = "button";
  cancel.addEventListener("click", () => closeWin(box, false));
  const next = mk("button", "ew-pri", "");
  next.id = "ew-next";
  next.type = "button";
  next.addEventListener("click", () => onNext(box));
  foot.append(back, hint, err, mk("span", "ew-sp"), cancel, next);
  win.append(head, prog, steps, body, foot);
  scrim.appendChild(win);
  scrim.addEventListener("mousedown", (event) => { if (event.target === scrim) closeWin(box, false); });
  box.appendChild(scrim);
  fillPages(box, win);
  body.addEventListener("input", () => { if (document.getElementById("ew-scrim").classList.contains("open")) paint2(box); });
}

// repaint the dirty badge and the review button without leaving the page
function paint2(box) {
  const s = save();
  const dirty = s ? s.formDirty(box) : false;
  document.getElementById("ew-dirty").classList.toggle("on", dirty);
  if (step === PAGES.length) {
    document.getElementById("ew-next").disabled = !dirty;
  }
}

function enhance(box) {
  if (!box || !box.querySelector(".confbox") || box.querySelector(".ew-summary")) return;
  if (document.getElementById("ew-scrim")) return;          // already open, being rebuilt
  const confbox = box.querySelector(".confbox");
  const card = summaryCard(box);
  confbox.before(card);
  buildWindow(box);
  confbox.hidden = true;
  const heading = confbox.previousElementSibling && confbox.previousElementSibling.previousElementSibling;
  if (heading && heading.tagName === "H3") heading.hidden = true;
}

let watching = false;
function watch() {
  if (watching || typeof MutationObserver === "undefined") return;
  const root = document.getElementById("board");
  if (!root) return;
  watching = true;
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    Promise.resolve().then(() => {
      queued = false;
      enhance(document.getElementById("boardmission"));
    });
  }).observe(root, { childList: true, subtree: true });
}

const BoardEditWin = { enhance, openWin, closeWin, PAGES, ROLES };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardEditWin;
} else if (typeof document !== "undefined") {
  globalThis.BoardEditWin = BoardEditWin;
  watch();
}
})();
