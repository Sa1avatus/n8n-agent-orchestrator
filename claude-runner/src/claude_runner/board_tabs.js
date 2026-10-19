/* board-scope-wrapped */
(function () {
/* Pages of the mission screen: one block at a time, a tab bar on top and a
 * previous/next pager at the bottom.  The panels are created by the other
 * board_*.js files (BoardMission, BoardTasks, ...); this module only groups
 * them and shows one group, nothing else is changed in them.
 *
 * BoardTabs.apply() is called by the shell after the mission screen is drawn;
 * BoardTabs.clear() removes the bars (the task screen and the views). */

const KEY = "ahawr-board-tab";
const PAGES = [
  { id: "launch", label: "tabs.launch", panels: ["boardmission"] },
  { id: "tasks", label: "tabs.tasks", panels: ["boardtasks", "boardplanedit"] },
  { id: "think", label: "tabs.think", panels: ["boardthink"] },
  { id: "ready", label: "tabs.ready", panels: ["boardready"] },
  { id: "hist", label: "tabs.hist", panels: ["boardhistory"] },
  { id: "claude", label: "tabs.claude", panels: ["boardclaudem"] },
];
const ALL_PANELS = PAGES.flatMap((p) => p.panels);
let pages = [];
let current = 0;

function tr(key) {
  return typeof t === "function" ? t(key) : key;
}

function panel(id) {
  return document.getElementById(id);
}

function remember(id) {
  try { sessionStorage.setItem(KEY, id); } catch (e) { /* private mode */ }
}

function recall() {
  try { return sessionStorage.getItem(KEY) || "launch"; } catch (e) { return "launch"; }
}

function node(tag, attrs, text) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}

function clear() {
  for (const id of ["boardtabs", "boardpager"]) {
    const n = panel(id);
    if (n) n.remove();
  }
}

function show(index) {
  if (!pages.length) return;
  current = Math.max(0, Math.min(pages.length - 1, index));
  const page = pages[current];
  for (const id of ALL_PANELS) {
    const p = panel(id);
    if (p) p.hidden = !page.panels.includes(id);
  }
  const nav = panel("boardtabs");
  if (nav) {
    [...nav.children].forEach((b, i) => {
      b.classList.toggle("on", i === current);
      b.setAttribute("aria-selected", i === current ? "true" : "false");
    });
  }
  const pager = panel("boardpager");
  if (pager) {
    pager.querySelector(".pg-prev").disabled = current === 0;
    pager.querySelector(".pg-next").disabled = current === pages.length - 1;
    pager.querySelector(".pg-pos").textContent = (current + 1) + " / " + pages.length + " · " + tr(page.label);
  }
  remember(page.id);
}

function apply() {
  clear();
  const root = panel("board");
  if (!root) return;
  pages = PAGES.filter((p) => p.panels.some((id) => panel(id)));
  if (pages.length < 2) return;
  const first = panel(pages[0].panels.find((id) => panel(id)));
  const nav = node("div", { id: "boardtabs", role: "tablist" });
  pages.forEach((p, i) => {
    const b = node("button", { type: "button", role: "tab", class: "bt-tab" }, tr(p.label));
    b.addEventListener("click", () => show(i));
    nav.appendChild(b);
  });
  root.insertBefore(nav, first);
  const pager = node("div", { id: "boardpager" });
  const prev = node("button", { type: "button", class: "pg-prev" }, "‹ " + tr("tabs.prev"));
  const pos = node("span", { class: "pg-pos" });
  const next = node("button", { type: "button", class: "pg-next" }, tr("tabs.next") + " ›");
  prev.addEventListener("click", () => { show(current - 1); window.scrollTo(0, 0); root.scrollTop = 0; });
  next.addEventListener("click", () => { show(current + 1); window.scrollTo(0, 0); root.scrollTop = 0; });
  pager.append(prev, pos, next);
  root.appendChild(pager);
  const wanted = pages.findIndex((p) => p.id === recall());
  show(wanted >= 0 ? wanted : 0);
}

globalThis.BoardTabs = { apply, clear, show };
})();
