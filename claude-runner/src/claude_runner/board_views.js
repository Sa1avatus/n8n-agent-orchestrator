/* board-scope-wrapped */
(function () {
/* Board view switcher and shared summary loader (T008).
 *
 * Loaded by board.html before board.js.  Hashes #board/view/<view>?project=<key>
 * belong to this file; the listeners it registers first stop board.js from
 * routing them, while #board and #board/<mission_id> pass through unchanged.
 */
"use strict";

const SUMMARY_URL = "/api/board/summary";
const REFRESH_MS = 15000;
const DEFAULT_VIEW = "proj";
const THEME_KEY = "ahawr-dashboard-theme";
const THEMES = ["auto", "light", "dark"];
const VIEW_TABS = [
  ["proj", "tab.proj"],
  ["kan", "tab.kan"],
  ["lane", "tab.lane"],
  ["spr", "tab.spr"],
  ["grid", "tab.grid"],
];

const views = new Map();
const state = { view: null, project: null, query: "", data: null, json: "", stale: false };

function tr(key, params) {
  return typeof globalThis.t === "function" ? globalThis.t(key, params) : key;
}

function el(tag, attrs, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children || []) if (child) node.append(child);
  return node;
}

function register(name, impl) {
  views.set(name, {
    render: impl.render,
    select: impl.select || null,
    open: impl.open || null,
  });
}

function parseViewHash(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const q = raw.indexOf("?");
  const parts = (q >= 0 ? raw.slice(0, q) : raw).split("/");
  if (parts[0] !== "board" || parts[1] !== "view" || !parts[2]) return null;
  let view;
  try {
    view = decodeURIComponent(parts[2]);
  } catch (e) {
    return null;
  }
  const query = new URLSearchParams(q >= 0 ? raw.slice(q + 1) : "");
  return { view, project: query.get("project") || null };
}

function buildViewHash(view, project) {
  let hash = "#board/view/" + encodeURIComponent(view);
  if (project) hash += "?project=" + encodeURIComponent(project);
  return hash;
}

function currentTheme() {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    return THEMES.includes(saved) ? saved : "auto";
  } catch (e) {
    return "auto";
  }
}

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === "light" || mode === "dark") root.setAttribute("data-theme", mode);
  else root.removeAttribute("data-theme");
}

function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  try {
    localStorage.setItem(THEME_KEY, next);
  } catch (e) {
    /* theme still applies for this page */
  }
  applyTheme(next);
  return next;
}

function themeLabel() {
  return tr("theme", { mode: tr("theme." + currentTheme()) });
}

function missionsOf(data) {
  return data && Array.isArray(data.missions) ? data.missions : [];
}

function projectsOf(data) {
  return data && Array.isArray(data.projects) ? data.projects : [];
}

function makeCard(mission) {
  const name = mission.name || mission.mission_id;
  const project = mission.project && mission.project.key ? mission.project.key : "";
  return el("button", {
    type: "button",
    class: "bv-card",
    "data-id": mission.mission_id,
    "data-project": project,
    "data-search": (name + " " + mission.mission_id).toLowerCase(),
    text: name,
  });
}

function note() {
  return el("p", { class: "bv-note", text: tr("part2.note") });
}

function drawHeader(host) {
  const languages = globalThis.BoardI18n && globalThis.BoardI18n.languages;
  const current = globalThis.BoardI18n ? globalThis.BoardI18n.getLanguage() : "en";
  const projects = projectsOf(state.data);
  host.replaceChildren(
    el("span", { class: "bv-title", text: tr("app.title") }),
    el("nav", { class: "bv-tabs", "aria-label": tr("switcher.label") },
      VIEW_TABS.map(([name, key]) => el("button", {
        type: "button",
        class: "bv-tab" + (name === state.view ? " on" : ""),
        "data-view": name,
        "aria-pressed": name === state.view ? "true" : "false",
        text: tr(key),
      }))),
    el("div", { class: "bv-chips" }, [
      el("button", {
        type: "button",
        class: "bv-chip" + (state.project ? "" : " on"),
        "aria-pressed": state.project ? "false" : "true",
        text: tr("filter.all"),
      }),
      ...projects.map((p) => el("button", {
        type: "button",
        class: "bv-chip" + (state.project === p.key ? " on" : ""),
        "data-project": p.key,
        "aria-pressed": state.project === p.key ? "true" : "false",
        text: p.name || p.key,
      })),
    ]),
    el("input", {
      type: "search",
      class: "bv-search",
      placeholder: tr("search"),
      "aria-label": tr("search"),
      value: state.query,
    }),
    el("select", { class: "bv-lang", "aria-label": tr("tr.lang") },
      (languages || [{ code: "en", name: "English" }]).map((lang) => el("option", {
        value: lang.code,
        selected: lang.code === current,
        text: lang.name,
      }))),
    el("button", { type: "button", id: "bvtheme", class: "bv-theme", text: themeLabel() }),
    el("button", { type: "button", class: "bv-tr", text: tr("tr.open") }),
  );
}

function syncHeader(host) {
  const inView = state.view !== null;
  host.querySelectorAll(".bv-tab").forEach((tab) => {
    const on = inView && tab.dataset.view === state.view;
    tab.classList.toggle("on", on);
    tab.setAttribute("aria-pressed", on ? "true" : "false");
  });
  host.querySelectorAll(".bv-chip").forEach((chip) => {
    const on = chip.dataset.project ? chip.dataset.project === state.project : !state.project;
    chip.classList.toggle("on", on);
    chip.setAttribute("aria-pressed", on ? "true" : "false");
  });
}

function applyFilter(body) {
  const q = state.query.trim().toLowerCase();
  body.querySelectorAll(".bv-card").forEach((card) => {
    const projectOk = !state.project || card.dataset.project === state.project;
    const queryOk = !q || card.dataset.search.includes(q);
    card.hidden = !(projectOk && queryOk);
  });
}

function drawBody(body) {
  body.replaceChildren();
  const impl = views.get(state.view);
  if (impl) {
    impl.render(body, {
      data: state.data,
      missions: missionsOf(state.data),
      projects: projectsOf(state.data),
      project: state.project,
      query: state.query,
      el,
      card: makeCard,
      tr,
    });
  } else {
    body.append(note());
  }
  applyFilter(body);
}

function ensureBody(host) {
  let body = document.getElementById("boardviewbody");
  if (!body) {
    body = el("div", { id: "boardviewbody" });
    host.after(body);
  }
  return body;
}

function busy() {
  const dnd = globalThis.BoardDnd;
  if (dnd && dnd.DndState && dnd.DndState.dragId != null) return true;
  const save = globalThis.BoardMissionSave;
  const box = document.getElementById("boardmission");
  return !!(save && typeof save.formDirty === "function" && box && save.formDirty(box));
}

async function fetchSummary() {
  const res = await fetch(SUMMARY_URL, {
    headers: { Accept: "application/json" },
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error("summary " + res.status);
  return res.json();
}

async function refreshOnce(deps) {
  const data = await deps.fetchSummary();
  const json = JSON.stringify(data);
  const changed = json !== state.json;
  state.data = data;
  state.json = json;
  if (deps.busy()) {
    state.stale = true;
    return "deferred";
  }
  if (changed || state.stale) {
    state.stale = false;
    deps.redraw();
    return "redrawn";
  }
  return "unchanged";
}

function setBodyMode(inView) {
  const body = document.getElementById("boardviewbody");
  const shell = document.getElementById("boardstate");
  if (body) body.hidden = !inView;
  if (shell) shell.hidden = inView;
  const queue = document.getElementById("boardqueue");
  if (queue) queue.hidden = inView;
}

function redraw(host) {
  drawHeader(host);
  if (state.view !== null) drawBody(ensureBody(host));
}

function navigate(view, project) {
  location.hash = buildViewHash(view, project);
}

function wireHeader(host) {
  host.addEventListener("click", (event) => {
    const tab = event.target.closest(".bv-tab");
    if (tab) return navigate(tab.dataset.view, state.project);
    const chip = event.target.closest(".bv-chip");
    if (chip) {
      const project = chip.dataset.project || null;
      if (state.view === null) return navigate(DEFAULT_VIEW, project);
      state.project = project;
      syncHeader(host);
      history.replaceState(null, "", buildViewHash(state.view, state.project));
      applyFilter(ensureBody(host));
      return;
    }
    const themeButton = event.target.closest("#bvtheme");
    if (themeButton) {
      cycleTheme();
      themeButton.textContent = themeLabel();
      return;
    }
    if (event.target.closest(".bv-tr")) {
      document.dispatchEvent(new CustomEvent("boardviews:translations"));
    }
  });
  host.addEventListener("input", (event) => {
    if (!event.target.classList.contains("bv-search")) return;
    state.query = event.target.value;
    applyFilter(ensureBody(host));
  });
  host.addEventListener("change", (event) => {
    if (!event.target.classList.contains("bv-lang")) return;
    globalThis.BoardI18n.setLanguage(event.target.value);
  });
}

function wireBody(body) {
  body.addEventListener("click", (event) => {
    const card = event.target.closest(".bv-card");
    if (!card) return;
    body.querySelectorAll(".bv-card.sel").forEach((c) => c.classList.remove("sel"));
    card.classList.add("sel");
    const impl = views.get(state.view);
    if (impl && impl.select) impl.select(card);
  });
  body.addEventListener("dblclick", (event) => {
    const card = event.target.closest(".bv-card");
    if (!card) return;
    const impl = views.get(state.view);
    if (impl && impl.open) impl.open(card.dataset.id, card);
    else location.hash = "#board/" + encodeURIComponent(card.dataset.id);
  });
}

function onRoute(event) {
  let parsed = parseViewHash(location.hash);
  if (!parsed && /^(#board)?$/.test(location.hash) && typeof history !== "undefined" && history.replaceState) {
    // the board opens on the Projects view (the old flat queue list stays behind the Kanban/Lanes views)
    history.replaceState(null, "", buildViewHash(DEFAULT_VIEW, null));
    parsed = parseViewHash(location.hash);
  }
  const host = document.getElementById("boardviews");
  if (!parsed) {
    state.view = null;
    state.project = null;
    if (host) syncHeader(host);
    setBodyMode(false);
    return;
  }
  if (event && event.stopImmediatePropagation) event.stopImmediatePropagation();
  state.view = parsed.view;
  state.project = parsed.project;
  if (!host) return;
  const body = ensureBody(host);
  setBodyMode(true);
  drawBody(body);
  syncHeader(host);
}

function init(host) {
  wireHeader(host);
  wireBody(ensureBody(host));
  // Skip the redraw while a drag is active or the form is dirty: a
  // re-render in that state would break the drop or lose the edits.
  const redrawHost = () => { if (!busy()) redraw(host); };
  if (globalThis.BoardI18n) {
    if (typeof globalThis.BoardI18n.onChange === "function") {
      globalThis.BoardI18n.onChange(redrawHost);
    }
    if (typeof globalThis.BoardI18n.load === "function") {
      globalThis.BoardI18n.load().then(redrawHost);
    }
  }
  // Render the header before the first summary arrives, so it shows even
  // when /api/board/summary is slow or fails.
  redrawHost();
  const tick = () => refreshOnce({ fetchSummary, busy, redraw: redrawHost }).catch(() => {});
  tick();
  setInterval(tick, REFRESH_MS);
  onRoute(null);
}

const BoardViews = {
  register,
  parseViewHash,
  buildViewHash,
  refreshOnce,
  busy,
  fetchSummary,
  init,
  DEFAULT_VIEW,
  REFRESH_MS,
  SUMMARY_URL,
};

if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardViews;
} else {
  globalThis.BoardViews = BoardViews;
  window.addEventListener("hashchange", onRoute);
  window.addEventListener("load", onRoute);
  const host = document.getElementById("boardviews");
  if (host) init(host);
}
})();
