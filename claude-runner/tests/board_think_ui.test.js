// Node test for board_think_ui.js (the thinking-policy panel).
// Run: node claude-runner/tests/board_think_ui.test.js
//
// Proves:
//   1. a bad policy shows the server's validation problems (stubbed
//      POST /api/board/thinking/policy);
//   2. the layout preview posts /thinking/variant exactly 12 times,
//      never more than 3 at once, and renders 12 rows;
//   3. the statistics render the uplift, the server's "мало данных,
//      нужно ещё N" text, and the MIN_ARM fallback when no data arrives;
//   4. a hostile variant name is escaped and never appears as markup;
//   5. after a Validate re-render the Preview/Statistics buttons still
//      work and the layout and stats are kept;
//   6. GET /api/board/missions/{id} is accepted in both shapes
//      ({mission: {...}, config: {...}} and a flat {mission_id, config});
//   7. a missing/unreachable mission renders the escaped error state
//      and flags the shell's #boardstate line.

"use strict";
const path = require("path");
const BT = require(path.join(__dirname, "..", "src", "claude_runner", "board_think_ui.js"));

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

// --- container stub (innerHTML + querySelector + dataset + appendChild) ---
// querySelector derives elements from the rendered innerHTML: a selector
// matches only if its class/attribute is present in the HTML, so a template
// that lacks a button makes querySelector return null and the test fails.
function makeContainer() {
  const stubs = {};
  function makeStub() {
    const s = {
      className: "", textContent: "", value: "", handlers: {},
      appendChild: () => {},
      addEventListener: (ev, fn) => { s.handlers[ev] = fn; },
      click: () => { if (s.handlers.click) s.handlers.click(); },
    };
    return s;
  }
  const c = {
    innerHTML: "",
    dataset: {},
    appendChild: () => {},
  };
  c.querySelector = (sel) => {
    const cls = sel.match(/^\.([\w-]+)$/);
    const name = sel.match(/^\[name="([\w-]+)"\]$/);
    const html = c.innerHTML;
    const ok = cls ? html.indexOf('class="' + cls[1] + '"') !== -1
      : name ? html.indexOf('name="' + name[1] + '"') !== -1
      : false;
    if (!ok) return null;
    return stubs[sel] || (stubs[sel] = makeStub());
  };
  return c;
}

// --- fetch stub: routes + in-flight counter ---
let inflight = 0;
let maxInflight = 0;
const fetchLog = [];
let statsBody;      // body for /thinking/stats
let thompsonBody;   // body for /thinking/thompson
let policyProblems; // problems for /thinking/policy

globalThis.fetch = async (url, opts) => {
  const u = String(url);
  inflight += 1;
  if (inflight > maxInflight) maxInflight = inflight;
  fetchLog.push({ url: u, method: (opts && opts.method) || "GET",
    body: opts && opts.body ? opts.body : null });
  await new Promise((r) => setTimeout(r, 5));
  const done = () => { inflight -= 1; };
  const jsonOf = (payload) => ({ ok: true, status: 200, json: async () => { done(); return payload; } });
  if (u === "/api/board/missions/m_running") {
    done();
    return jsonOf({
      mission: { mission_id: "m_running" },
      live_state: null, task_views: [],
      config: { worker_think_policy: { variants: [{ name: "v1", weight: 1 }], from_task: 1 } },
      task_overrides: {},
    });
  }
  if (u === "/api/board/missions/m_flat") {
    done();
    return jsonOf({
      mission_id: "m_flat",
      config: { worker_think_policy: null },
    });
  }
  if (u === "/api/board/thinking/policy") {
    done();
    return jsonOf({ valid: false, problems: policyProblems });
  }
  if (u === "/api/board/thinking/variant") {
    const body = JSON.parse(opts.body);
    done();
    return jsonOf({ variant: "v" + body.task_number });
  }
  if (u === "/api/board/thinking/stats") {
    done();
    return jsonOf(statsBody);
  }
  if (u === "/api/board/thinking/thompson") {
    done();
    return jsonOf(thompsonBody);
  }
  // a detail that carries no mission id at all -> the error state
  if (u === "/api/board/missions/m_missing") {
    done();
    return { ok: true, status: 200, json: async () => ({}) };
  }
  done();
  throw new Error("unexpected url: " + u);
};

const stateStub = { textContent: "", className: "" };
globalThis.document = {
  getElementById: (id) => (id === "boardstate" ? stateStub : null),
  createElement: () => ({ className: "", textContent: "", dataset: {}, appendChild: () => {}, querySelector: () => null }),
};

(async () => {
  // --- 1. the panel loads from the mission config and a bad policy
  //        shows the server's problems ---
  const c1 = makeContainer();
  await BT.render(c1, "m_running");
  check("the policy text is initialized from config.worker_think_policy",
    has(c1.innerHTML, "variants") && has(c1.innerHTML, "v1"));

  // set the textarea value to a broken policy and click Validate.
  const ta = c1.querySelector('[name="think_policy"]');
  ta.value = '{ "variants": [ { "name": 5 } ] }';
  policyProblems = ["variant name must be a string", "weight must be a whole number 1..20"];
  const vb = c1.querySelector(".tk-validate");
  check("the Validate button exists", !!vb);
  vb.click();
  await new Promise((r) => setTimeout(r, 50));
  check("the bad policy shows the server's problems",
    has(c1.innerHTML, escOf("variant name must be a string")) &&
    has(c1.innerHTML, escOf("weight must be a whole number 1..20")));
  check("the problems are not raw markup",
    !c1.innerHTML.includes("<img"));
  const policyPost = fetchLog.find((f) => f.url === "/api/board/thinking/policy");
  check("the Validate request body carries the parsed policy from the textarea",
    !!policyPost && policyPost.method === "POST" &&
    JSON.stringify(JSON.parse(policyPost.body)) ===
      JSON.stringify({ policy: { variants: [{ name: 5 }] } }));

  // --- 2. the layout preview: 12 POSTs, at most 3 concurrent, 12 rows ---
  const lb = c1.querySelector(".tk-layout");
  check("the Preview layout button exists", !!lb);
  lb.click();
  await new Promise((r) => setTimeout(r, 200));
  const variantPosts = fetchLog.filter((f) => f.url === "/api/board/thinking/variant");
  check("the layout preview posts /thinking/variant exactly 12 times",
    variantPosts.length === 12);
  check("never more than 3 variant POSTs are in flight at once",
    maxInflight <= 3);
  check("12 layout rows render (tasks 1-12)",
    (c1.innerHTML.match(/class="tktask"/g) || []).length === 12 &&
    has(c1.innerHTML, ">1</td>") && has(c1.innerHTML, ">12</td>") &&
    has(c1.innerHTML, ">v1</td>") && has(c1.innerHTML, ">v12</td>"));
  check("each layout POST carries the policy and its task number (1-12)",
    variantPosts.every((f) => {
      const b = JSON.parse(f.body);
      return b.policy !== undefined && b.task_number !== undefined;
    }) &&
    variantPosts.map((f) => JSON.parse(f.body).task_number).join(",")
      === "1,2,3,4,5,6,7,8,9,10,11,12");

  // --- 3. the statistics: uplift text, the server's мало-данных text,
  //        and the MIN_ARM fallback when no data arrives ---
  const sb = c1.querySelector(".tk-stats");
  check("the Statistics button exists", !!sb);
  // no data at all: the fallback line with N = MIN_ARM = 100
  statsBody = { variants: [], control: "off" };
  thompsonBody = { thompson: [] };
  sb.click();
  await new Promise((r) => setTimeout(r, 100));
  check("without attempt data the not-enough-data fallback shows N = 100",
    has(c1.innerHTML, "not enough data, 100 more attempts per variant needed"));

  // a stubbed /thinking/stats answer: one variant with the server's text,
  // one with the numeric uplift, one the control.
  statsBody = {
    control: "control",
    variants: [
      { variant: "always-think", attempts: 120, passes: 84, acceptance_share: 0.7,
        minutes_per_attempt: 22.5, thinking_tokens: 9600000,
        uplift: { pass_diff_pct: 12.5, pass_interval: [6.9, 18.7] } },
      { variant: "off_first", attempts: 12, passes: 7, acceptance_share: 0.583,
        minutes_per_attempt: 25.1, thinking_tokens: 42000,
        uplift: { text: "мало данных, нужно ещё 88 попыток на ветку" } },
      { variant: "control", attempts: 120, passes: 66, acceptance_share: 0.55,
        minutes_per_attempt: 26.7, thinking_tokens: 0, uplift: null },
    ],
  };
  thompsonBody = {
    thompson: [
      { variant: "always-think", attempts: 120, passes: 84, p_best: 0.9,
        rate_per_hour: 4.0, suggested_weight: 7 },
      { variant: "control", attempts: 120, passes: 66, p_best: 0.1,
        rate_per_hour: 2.0, suggested_weight: 1 },
    ],
  };
  sb.click();
  await new Promise((r) => setTimeout(r, 100));
  check("the numeric uplift renders with its 90% interval",
    has(c1.innerHTML, "+12.5 pp (90%: 6.9…18.7)"));
  check("the server's not-enough-data text renders with its N",
    has(c1.innerHTML, "not enough data, 88 more attempts per variant needed"));
  check("the control row shows 'control' in the uplift column",
    has(c1.innerHTML, "<td class=\"tkuplift\">control</td>"));
  check("the suggested weights render as plain text",
    has(c1.innerHTML, "recommended weights (Thompson, seed 7): always-think 7, control 1"));

  // --- 4. a hostile variant name is escaped, never markup ---
  statsBody = {
    control: "off",
    variants: [{ variant: "<img src=x onerror=alert(1)>", attempts: 1, passes: 1,
      acceptance_share: 1.0, minutes_per_attempt: 1.0, thinking_tokens: 0,
      uplift: { text: "мало данных, нужно ещё 99 попыток на ветку" } }],
  };
  thompsonBody = { thompson: [] };
  sb.click();
  await new Promise((r) => setTimeout(r, 100));
  check("a hostile variant name is escaped in the row",
    has(c1.innerHTML, escOf("<img src=x onerror=alert(1)>")));
  check("the hostile variant never appears as raw markup",
    !c1.innerHTML.includes("<img src=x onerror=alert(1)>"));
  check("the stats and thompson POST bodies carry the policy and an empty attempts list",
    fetchLog.filter((f) =>
      f.url === "/api/board/thinking/stats" || f.url === "/api/board/thinking/thompson")
      .every((f) => {
        const b = JSON.parse(f.body);
        return b.policy !== undefined && Array.isArray(b.attempts) && b.attempts.length === 0;
      }));

  // --- 5. after a Validate re-render the controls still work and the
  //        layout and stats are kept ---
  const beforeLayout = (c1.innerHTML.match(/class="tktask"/g) || []).length;
  const beforeStats = has(c1.innerHTML, "not enough data, 99 more attempts per variant needed");
  check("before the second validate the layout (12 rows) and stats are present",
    beforeLayout === 12 && beforeStats);
  policyProblems = ["broken JSON"];
  vb.click();
  await new Promise((r) => setTimeout(r, 100));
  check("the second Validate click posted again",
    fetchLog.filter((f) => f.url === "/api/board/thinking/policy").length === 2);
  check("the second Validate shows the new problems",
    has(c1.innerHTML, escOf("broken JSON")));
  check("the layout rows are kept after the re-render",
    (c1.innerHTML.match(/class="tktask"/g) || []).length === 12);
  check("the statistics are kept after the re-render",
    has(c1.innerHTML, "not enough data, 99 more attempts per variant needed"));
  check("the Preview and Statistics buttons still exist after the re-render",
    !!c1.querySelector(".tk-layout") && !!c1.querySelector(".tk-stats"));

  // --- 6. the flat mission shape is accepted too ---
  const c2 = makeContainer();
  await BT.render(c2, "m_flat");
  check("a flat {mission_id, config} detail renders the panel",
    has(c2.innerHTML, "Thinking policy"));

  // --- 7. a missing mission renders the escaped error state ---
  const c3 = makeContainer();
  await BT.render(c3, "m_missing");
  check("a missing mission renders the .tkerror state with the escaped message",
    !!c3.querySelector(".tkerror") && has(c3.innerHTML, escOf("mission not found")));
  check("the error is not raw markup", !c3.innerHTML.includes("<img"));
  check("the shell's #boardstate line is flagged with the error",
    stateStub.textContent === "mission not found" && stateStub.className === "error");

  delete globalThis.document;
  delete globalThis.fetch;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
