// Node test for board_history_ui.js (the change history panel).
// Run: node claude-runner/tests/board_history_ui.test.js
//
// Proves:
//   1. the list renders: entries are fetched from GET /api/board/changelog
//      and each entry row shows its timestamp, who and action;
//   2. clicking Rollback shows the diff in the entry's .chdiffbox;
//   3. declining the confirmation sends nothing (no POST in flight);
//   4. accepting sends exactly POST /api/board/changelog/{id}/rollback
//      with the X-AHAWR-Dashboard header;
//   5. a 409 answer shows the server's conflict text in the panel;
//   6. a hostile value (markup) is escaped and never appears as raw HTML;
//   7. the empty and error states render.

"use strict";
const path = require("path");
const BH = require(path.join(__dirname, "../src/claude_runner/board_history_ui.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}
function escOf(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// --- container stub: innerHTML + querySelector + querySelectorAll ---
// querySelector derives elements from the rendered innerHTML: a selector
// matches only if its class is present in the HTML, so a template that
// lacks the element makes querySelector return null and the test fails.
function makeContainer() {
  const stubs = {};
  // Each rendered class gets one shared stub, keyed by its selector: the
  // container's querySelector(".chdiffbox") and the rollback handler's
  // closest(".chentry").querySelector(".chdiffbox") both resolve to the
  // same stub, so the diff written into the box is visible to the test.
  function makeStub() {
    const s = {
      className: "", textContent: "", value: "",
      innerHTML: "", dataset: {}, handlers: {},
      appendChild: () => {},
      addEventListener: (ev, fn) => { s.handlers[ev] = fn; },
      click: () => { if (s.handlers.click) s.handlers.click(); },
      // the button lives inside its .chentry row; closest(".chentry")
      // resolves to the shared row stub so the handler can reach the
      // row's .chdiffbox (shared per class, so the diff written into it
      // is the same stub the container's querySelector(".chdiffbox")
      // returns and the test can read it).
      closest: (cls) => {
        const key = cls[0] === "." ? cls : "." + cls;
        return stubs[key] || (stubs[key] = makeStub());
      },
      querySelector: (sel) => {
        const cls = sel.match(/^\.([\w-]+)$/);
        if (!cls) return null;
        return stubs["." + cls[1]] || (stubs["." + cls[1]] = makeStub());
      },
    };
    return s;
  }
  const c = {
    innerHTML: "",
    dataset: {},
    appendChild: () => {},
    querySelector: (sel) => {
      const cls = sel.match(/^\.([\w-]+)$/);
      if (!cls) return null;
      const ok = c.innerHTML.indexOf('class="' + cls[1] + '"') !== -1;
      if (!ok) return null;
      const s = stubs[sel] || (stubs[sel] = makeStub());
      // the result line: the handler sets textContent on it, so the
      // container's innerHTML must carry the result text for the test
      // to read it.
      if (cls[1] === "chresult") {
        let t = "";
        Object.defineProperty(s, "textContent", {
          get: () => t,
          set: (v) => { t = v; c.innerHTML += v; },
        });
      }
      return s;
    },
    querySelectorAll: (sel) => {
      const cls = sel.match(/^\.([\w-]+)$/);
      if (!cls) return [];
      // match the class with a closing quote so "class="chentries"" is not
      // counted as a "chentry".
      const re = new RegExp('class="' + cls[1] + '"', "g");
      const count = (c.innerHTML.match(re) || []).length;
      const list = [];
      for (let i = 0; i < count; i++) {
        const key = sel + "#" + i;
        const st = stubs[key] || (stubs[key] = makeStub());
        st.className = cls[1];
        // the button id is read from the data-id attribute: count the
        // data-id="i" occurrences in order and take the i-th.
        const ids = c.innerHTML.match(/data-id="([^"]*)"/g) || [];
        st.dataset = { id: ids.length ? ids[i].replace(/data-id="/, "").replace(/"/, "") : "" };
        list.push(st);
      }
      return list;
    },
  };
  return c;
}

// --- fetch stub: routes, per-URL counters, in-flight counter ---
let inflight = 0;
const fetchLog = [];
let changelogBody;   // body for GET /api/board/changelog
let rollbackStatus;   // status of the next POST /api/board/changelog/{id}/rollback
let rollbackBody;     // body of that POST

globalThis.fetch = async (url, opts) => {
  const u = String(url);
  inflight += 1;
  fetchLog.push({
    url: u,
    method: (opts && opts.method) || "GET",
    headers: opts && opts.headers ? opts.headers : {},
    body: opts && opts.body ? opts.body : null,
  });
  const done = () => { inflight -= 1; };
  const jsonOf = (payload, status) => ({
    ok: (status || 200) < 400, status: status || 200,
    json: async () => { done(); return payload; },
  });
  if (u === "/api/board/changelog") {
    return jsonOf(changelogBody, 200);
  }
  const rb = u.match(/^\/api\/board\/changelog\/([^/]+)\/rollback$/);
  if (rb) {
    return jsonOf(rollbackBody, rollbackStatus);
  }
  if (u === "/api/board/changelog/error") {
    done();
    throw new Error("changelog unavailable");
  }
  done();
  throw new Error("unexpected url: " + u);
};

const stateStub = { textContent: "", className: "" };
globalThis.document = {
  getElementById: (id) => (id === "boardstate" ? stateStub : null),
  createElement: () => ({ className: "", textContent: "", dataset: {}, appendChild: () => {} }),
};
globalThis.window = globalThis;

(async () => {
  // --- 1. the list renders: two entries, newest first ---
  changelogBody = { changelog: [
    { id: "c2", at: "2026-10-18T10:00:00Z", who: "worker", action: "edit",
      mission_id: "m1", task_key: "t1",
      before_json: { a: 1 }, after_json: { a: 2 } },
    { id: "c1", at: "2026-10-18T09:00:00Z", who: "reviewer", action: "accept",
      mission_id: "m1", task_key: "t1",
      before_json: { b: "x" }, after_json: { b: "y" } },
  ] };
  const c1 = makeContainer();
  await BH.render(c1, "m1");
  check("the list renders its header", has(c1.innerHTML, "Change history"));
  check("both entries render their id and action",
    has(c1.innerHTML, 'data-id="c2"') && has(c1.innerHTML, "edit") &&
    has(c1.innerHTML, 'data-id="c1"') && has(c1.innerHTML, "accept"));
  check("each entry shows its who and timestamp",
    has(c1.innerHTML, "worker") && has(c1.innerHTML, "2026-10-18T10:00:00Z") &&
    has(c1.innerHTML, "reviewer") && has(c1.innerHTML, "2026-10-18T09:00:00Z"));

  // --- 2. rollback click shows the diff in the .chdiffbox ---
  const btns = c1.querySelectorAll(".ch-rollback");
  check("two rollback buttons are present", btns.length === 2);
  window.confirm = () => true;
  btns[0].click();
  await new Promise((r) => setTimeout(r, 50));
  const box = c1.querySelector(".chdiffbox");
  check("the diff renders in the entry's diffbox",
    !!box && has(box.innerHTML, "<table class=\"chdiff\">") &&
    has(box.innerHTML, ">a</td>") && has(box.innerHTML, "1") && has(box.innerHTML, "2"));
  check("the accepted rollback was sent",
    inflight === 0 &&
    fetchLog.some((f) => f.url === "/api/board/changelog/c2/rollback" && f.method === "POST"));
  rollbackStatus = 200;
  rollbackBody = { ok: true };
  // the POSTs above already resolved; re-click to observe the success text.
  btns[0].click();
  await new Promise((r) => setTimeout(r, 50));
  check("a successful rollback shows 'rolled back' in the result line",
    has(c1.innerHTML, "rolled back"));

  // --- 3. declining the confirmation sends nothing ---
  const rollbackCountBefore = fetchLog.filter(
    (f) => f.url === "/api/board/changelog/c2/rollback").length;
  window.confirm = () => false;
  btns[0].click();
  await new Promise((r) => setTimeout(r, 50));
  const rollbackCountAfter = fetchLog.filter(
    (f) => f.url === "/api/board/changelog/c2/rollback").length;
  check("declining the confirmation sends no POST",
    rollbackCountAfter === rollbackCountBefore && inflight === 0);

  // --- 4. accepting sends exactly the rollback POST ---
  window.confirm = () => true;
  btns[1].click();
  await new Promise((r) => setTimeout(r, 50));
  const post = fetchLog.find((f) => f.url === "/api/board/changelog/c1/rollback");
  check("the accepted rollback posts to /api/board/changelog/c1/rollback",
    !!post && post.method === "POST");
  check("the POST body is exactly {}",
    post && post.body === "{}");
  check("the POST carries the X-AHAWR-Dashboard header",
    post && post.headers["X-AHAWR-Dashboard"] === "1");
  check("the POST body has no extra keys",
    post && Object.keys(JSON.parse(post.body)).length === 0);

  // --- 5. a 409 shows the server's conflict text ---
  rollbackStatus = 409;
  rollbackBody = { error: { message: "row already changed" } };
  window.confirm = () => true;
  btns[1].click();
  await new Promise((r) => setTimeout(r, 50));
  check("a 409 shows the server's conflict text in the panel",
    has(c1.innerHTML, escOf("row already changed")));

  // --- 6. a hostile value is escaped and never appears as markup ---
  changelogBody = { changelog: [
    { id: "h1", at: "t", who: "<img src=x onerror=alert(1)>",
      action: "evil", mission_id: "m1", task_key: "t",
      before_json: null, after_json: null },
  ] };
  const c2 = makeContainer();
  await BH.render(c2, "m1");
  check("a hostile who is escaped",
    has(c2.innerHTML, escOf("<img src=x onerror=alert(1)>")));
  check("the hostile who never appears as raw markup",
    !c2.innerHTML.includes("<img src=x onerror=alert(1)>"));
  check("no <img> tag appears in the rendered panel",
    !c2.innerHTML.includes("<img"));

  // --- 7. the empty and error states ---
  // empty: the changelog has no entries for the requested mission.
  changelogBody = { changelog: [
    { id: "other", at: "t", who: "w", action: "a", mission_id: "m2",
      task_key: "t", before_json: null, after_json: null },
  ] };
  const c3 = makeContainer();
  await BH.render(c3, "m1");
  check("the empty state shows the header and no entry rows",
    has(c3.innerHTML, "Change history") &&
    (c3.innerHTML.match(/class="chentry"/g) || []).length === 0);
  check("no rollback button in the empty state",
    (c3.querySelectorAll(".ch-rollback")).length === 0);

  // error: the fetch throws and the escaped message renders.
  const c4 = makeContainer();
  globalThis.fetch = async () => { throw new Error("changelog unavailable"); };
  await BH.render(c4, "m1");
  check("the error state shows the escaped message",
    has(c4.innerHTML, escOf("changelog unavailable")));
  check("the error state is not raw markup", !c4.innerHTML.includes("<img"));

  delete globalThis.document;
  delete globalThis.fetch;
  delete globalThis.window;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
