// Node test: which exports of board_think_ui.js STAY, pinned on the code as it
// is today (T011, step 1 of the unused-export cleanup of the thinking panel).
// Run: node --test tests/board_think_ui_exports_stay.test.js
//
// Pins:
//   1. the exports board_mission.js (BoardThinkUI.render) and board.html
//    depend on are still exported and callable: render, renderHtml, renderInto,
//    esc, MIN_ARM;
//   2. the behaviour the audit proves is never read from outside the module
//    survives through the staying API: the Validate button posts the parsed
//    policy, the layout button previews tasks 1-12 with the parallel limit of
//    three, the statistics button renders the not-enough-data fallback built
//    from MIN_ARM.  Deleting the export entries of the internal helpers cannot
//    break this file; deleting a staying name, or the internal helper behind a
//    behaviour pinned here, can.

"use strict";
const path = require("path");
const BT = require(path.join(__dirname, "..", "src", "ahawr_runner", "board_think_ui.js"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}
function has(html, needle) {
  return html.indexOf(needle) !== -1;
}

// --- container stub (innerHTML + querySelector + appendChild), as in
// board_think_ui.test.js: a selector matches only if its markup is present. ---
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
  const c = { innerHTML: "", dataset: {}, appendChild: () => {} };
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

// --- fetch stub: routes + in-flight counter + POST headers ---
let inflight = 0;
let maxInflight = 0;
const posts = [];
let statsBody = { variants: [], control: "off" };
let thompsonBody = { thompson: [] };

globalThis.fetch = async (url, opts) => {
  const u = String(url);
  inflight += 1;
  if (inflight > maxInflight) maxInflight = inflight;
  if (opts && opts.method === "POST") {
    posts.push({ url: u, method: opts.method, body: opts.body, headers: opts.headers || {} });
  }
  await new Promise((r) => setTimeout(r, 5));
  const done = () => { inflight -= 1; };
  const jsonOf = (payload) => ({ ok: true, status: 200, json: async () => { done(); return payload; } });
  if (u === "/api/board/missions/t1") {
    return jsonOf({
      mission: { mission_id: "t1" },
      config: { worker_think_policy: { variants: [{ name: "v1", weight: 1 }] } },
    });
  }
  if (u === "/api/board/thinking/policy") { done(); return jsonOf({ valid: true, problems: [] }); }
  if (u === "/api/board/thinking/variant") {
    const body = JSON.parse(opts.body);
    return jsonOf({ variant: "v" + body.task_number });
  }
  if (u === "/api/board/thinking/stats") { done(); return jsonOf(statsBody); }
  if (u === "/api/board/thinking/thompson") { done(); return jsonOf(thompsonBody); }
  done();
  throw new Error("unexpected url: " + u);
};

const stateStub = { textContent: "", className: "" };
globalThis.document = {
  getElementById: (id) => (id === "boardstate" ? stateStub : null),
  createElement: () => ({ className: "", textContent: "", dataset: {}, appendChild: () => {}, querySelector: () => null }),
};

(async () => {
  // --- 1. the exports the board uses stay exported and callable ---
  for (const name of ["render", "renderHtml", "renderInto", "esc"]) {
    check(`BoardThinkUI.${name} is still exported as a function`, typeof BT[name] === "function");
  }
  check("BoardThinkUI.MIN_ARM is still exported with the server's value", BT.MIN_ARM === 100);

  // --- 2. a basic call of each staying export ---
  check("esc escapes hostile markup", BT.esc("<img src=x onerror=alert(1)>")
    === "&lt;img src=x onerror=alert(1)&gt;");
  const pureHtml = BT.renderHtml("{}", null, [], null, null);
  check("renderHtml renders the policy textarea and the three panel buttons",
    has(pureHtml, 'name="think_policy"') && has(pureHtml, 'class="tk-validate"') &&
    has(pureHtml, 'class="tk-layout"') && has(pureHtml, 'class="tk-stats"'));
  const box = makeContainer();
  BT.renderInto(box, "t1", "{\"variants\":[]}", null, null, null, null);
  check("renderInto writes the panel into the container it is given",
    has(box.innerHTML, "Thinking policy") && has(box.innerHTML, "No layout yet."));
  check("renderInto clears the shell's #boardstate line",
    stateStub.textContent === "" && stateStub.className === "");

  // --- 3. the behaviours the never-exported helpers produce, reached through
 //        the staying API (render -> renderInto -> renderThinkHtml and the
 //        control handlers attached by attachThinkControls) ---
  maxInflight = 0;
  const panel = makeContainer();
  await BT.render(panel, "t1");
  check("render loads the mission config into the policy textarea",
    has(panel.innerHTML, "variants") && has(panel.innerHTML, "v1"));

  const ta = panel.querySelector('[name="think_policy"]');
  ta.value = '{ "variants": [ { "name": "a", "weight": 2 } ] }';
  panel.querySelector(".tk-validate").click();
  await new Promise((r) => setTimeout(r, 50));
  const policyPost = posts.find((p) => p.url === "/api/board/thinking/policy");
  check("the Validate button posts the policy text parsed into an object",
    !!policyPost && policyPost.method === "POST" &&
    JSON.stringify(JSON.parse(policyPost.body)) === JSON.stringify({ policy: { variants: [{ name: "a", weight: 2 }] } }));
  check("every panel POST carries the dashboard header",
    policyPost && policyPost.headers["X-AHAWR-Dashboard"] === "1");

  panel.querySelector(".tk-layout").click();
  await new Promise((r) => setTimeout(r, 200));
  const variantPosts = posts.filter((p) => p.url === "/api/board/thinking/variant");
  check("the layout button previews all 12 tasks", variantPosts.length === 12);
  check("the layout preview never runs more than 3 variant POSTs at once",
    maxInflight <= 3);
  check("the layout rows render after the preview",
    (panel.innerHTML.match(/class="tktask"/g) || []).length === 12 &&
    has(panel.innerHTML, ">v12</td>"));

  panel.querySelector(".tk-stats").click();
  await new Promise((r) => setTimeout(r, 100));
  check("the statistics button renders the not-enough-data fallback with MIN_ARM",
    has(panel.innerHTML, "not enough data, 100 more attempts per variant needed"));
  check("the statistics POSTs carry the policy and an empty attempts list",
    posts.filter((p) => p.url === "/api/board/thinking/stats" || p.url === "/api/board/thinking/thompson")
      .every((p) => {
        const b = JSON.parse(p.body);
        return b.policy !== undefined && Array.isArray(b.attempts) && b.attempts.length === 0;
      }));

  statsBody = {
    control: "off",
    variants: [{ variant: "off_first", attempts: 12, passes: 7, acceptance_share: 0.58,
      minutes_per_attempt: 25.0, thinking_tokens: 42000,
      uplift: { text: "мало данных, нужно ещё 88 попыток на ветку" } }],
  };
  panel.querySelector(".tk-stats").click();
  await new Promise((r) => setTimeout(r, 100));
  check("the server's own not-enough-data number is kept in the rendered line",
    has(panel.innerHTML, "not enough data, 88 more attempts per variant needed"));

  delete globalThis.document;
  delete globalThis.fetch;
  console.log("all ok");
})().catch((e) => {
  console.error(e && e.stack || e);
  process.exit(1);
});
