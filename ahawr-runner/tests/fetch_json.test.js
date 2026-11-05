// Characterization tests for the fetchJson helper in board*.js (T001): the
// single definition in board_actions.js, the kept exception in board_dnd.js
// (its error text differs), and the modules that now bind the shared helper
// (board_history_ui.js, board_mission.js, board_mission_save.js,
// board_plan_edit.js, board_ready_ui.js).
// The helper is module-internal (board_actions.js and board_mission_save.js
// export it), so each body is extracted from the source text and run in a vm
// sandbox with a stubbed fetch: no network, no source change.  Run:
//   cd ahawr-runner && node --test tests/fetch_json.test.js
//
// Pinned behaviours per copy:
//   - request options: cache "no-store", no method/headers/body unless the
//     caller passes options (only board_actions.js takes options, and its
//     postJson passes X-AHAWR-Dashboard: 1);
//   - 2xx JSON body returned as-is;
//   - non-2xx: throws an Error carrying status/statusText and the parsed body;
//   - error text: board_dnd.js uses body.error raw (a string stays, an object
//     becomes "[object Object]"), every other copy uses body.error.message,
//     falling back to "HTTP <status>";
//   - a non-JSON body is swallowed into {} (json() failure), never thrown;
//   - no timer in the helper: fetch and res.json() are awaited directly.

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { test } = require("node:test");
const assert = require("node:assert");

const SRC = path.join(__dirname, "..", "src", "ahawr_runner");

// --- extract each helper once, with its exact source text (for the shape pins) ---
function extractFetchJson(file) {
  const text = fs.readFileSync(path.join(SRC, file), "utf8");
  const match = text.match(/async function fetchJson\(url(?:, options)?\) \{[\s\S]*?\n\}/);
  if (!match) throw new Error(`no fetchJson definition found in ${file}`);
  return match[0];
}

// Modules that no longer have their own copy: they bind board_actions.js's
// fetchJson, so the behaviour pins run the shared body under their name.
const SHARED_SOURCE = extractFetchJson("board_actions.js");

// The board*.js files board.html loads that carried a fetchJson helper.
const BOARD_FILES = ["board_actions.js", "board_dnd.js", "board_history_ui.js",
  "board_mission.js", "board_mission_save.js", "board_plan_edit.js", "board_ready_ui.js"];

const COPIES = {
  board_actions: { file: "board_actions.js", source: SHARED_SOURCE },
  board_dnd: { file: "board_dnd.js", source: extractFetchJson("board_dnd.js") },
  board_history_ui: { file: "board_history_ui.js", source: SHARED_SOURCE },
  board_mission: { file: "board_mission.js", source: SHARED_SOURCE },
  board_mission_save: { file: "board_mission_save.js", source: SHARED_SOURCE },
  board_plan_edit: { file: "board_plan_edit.js", source: SHARED_SOURCE },
  board_ready_ui: { file: "board_ready_ui.js", source: SHARED_SOURCE },
};

// --- stubbed fetch: records the call, returns canned responses, no network ---
function makeFetchStub(response) {
  const calls = [];
  const fetchStub = (url, options) => {
    calls.push({ url, options });
    return Promise.resolve({
      ok: response.ok,
      status: response.status,
      statusText: response.statusText || "",
      json: () => {
        if (response.jsonFails) return Promise.reject(new Error("not a JSON body"));
        return Promise.resolve(response.body);
      },
    });
  };
  return { fetchStub, calls };
}

// Run one copy with the stub in a sandbox; bound: the vm has no timers here and
// the helper awaits only the stub's resolved promises.  The vm context has its
// own primitives (Object, Error), so comparisons use JSON.stringify to avoid
// cross-context identity issues.
function runCopy(name, url, options, response) {
  const { fetchStub, calls } = makeFetchStub(response);
  const sandbox = vm.createContext({ fetch: fetchStub, Error });
  vm.runInContext(COPIES[name].source, sandbox, { filename: COPIES[name].file });
  return { fn: sandbox.fetchJson, calls };
}

// --- request options and headers ---
test("fetchJson copies send cache no-store with no method or headers (GET-only shape)", () => {
  for (const name of ["board_dnd", "board_history_ui", "board_mission",
    "board_mission_save", "board_plan_edit", "board_ready_ui"]) {
    const { fn, calls } = runCopy(name, "/made-up/queue", undefined,
      { ok: true, status: 200, body: { queue: [7] } });
    fn("/made-up/queue");
    assert.strictEqual(JSON.stringify(calls[0].options), JSON.stringify({ cache: "no-store" }), `${name} options`);
    // the source text itself proves no method/headers/X-AHAWR-Dashboard in the copy
    assert.ok(!COPIES[name].source.includes("X-AHAWR-Dashboard"), `${name} has no dashboard header`);
  }
});

// The X-AHAWR-Dashboard: 1 header is never inside fetchJson itself; it is set by
// the sibling mutation helper in each module (postJson / postQueueJson /
// planEditPost), which calls fetch directly.  Pinned here per module.
const POST_HELPERS = {
  board_actions: "postJson",
  board_dnd: "postQueueJson",
  board_history_ui: "historyPost",
  board_mission: "postJson",
  board_mission_save: "postJson",
  board_plan_edit: "planEditPost",
  board_ready_ui: "readyPost",
};

test("the mutation helper of each module sends X-AHAWR-Dashboard: 1 through fetch, not fetchJson", () => {
  for (const [name, helper] of Object.entries(POST_HELPERS)) {
    const text = fs.readFileSync(path.join(SRC, COPIES[name].file), "utf8");
    const match = text.match(new RegExp(`async function ${helper}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`));
    assert.ok(match, `${name}: ${helper} not found`);
    const body = match[0];
    assert.ok(body.includes('"X-AHAWR-Dashboard": "1"'), `${name}.${helper} sends the dashboard header`);
    assert.ok(body.includes("await fetch(url"), `${name}.${helper} calls fetch directly (not fetchJson)`);
  }
});

test("board_actions fetchJson merges caller options over cache no-store", async () => {
  const { fn, calls } = runCopy("board_actions", "/made-up/queue",
    { method: "POST", headers: { "X-AHAWR-Dashboard": "1" }, body: "{}" },
    { ok: true, status: 200, body: { version: "v9" } });
  const out = await fn("/made-up/queue", { method: "POST", headers: { "X-AHAWR-Dashboard": "1" }, body: "{}" });
  assert.strictEqual(JSON.stringify(out), JSON.stringify({ version: "v9" }));
  assert.strictEqual(calls[0].options.method, "POST");
  assert.strictEqual(calls[0].options.cache, "no-store");
  assert.strictEqual(JSON.stringify(calls[0].options.headers), JSON.stringify({ "X-AHAWR-Dashboard": "1" }));
  assert.strictEqual(calls[0].options.body, "{}");
});

test("board_actions fetchJson with no options still sends cache no-store only", async () => {
  const { fn, calls } = runCopy("board_actions", "/made-up/missions", undefined,
    { ok: true, status: 200, body: { missions: [] } });
  await fn("/made-up/missions");
  assert.strictEqual(JSON.stringify(calls[0].options), JSON.stringify({ cache: "no-store" }));
});

// --- 2xx JSON response ---
test("every fetchJson copy returns the parsed JSON body of a 2xx response", async () => {
  for (const name of Object.keys(COPIES)) {
    const { fn } = runCopy(name, "/made-up/missions", undefined,
      { ok: true, status: 200, body: { missions: [{ mission_id: "m-7" }] } });
    const out = await fn("/made-up/missions");
    assert.strictEqual(JSON.stringify(out), JSON.stringify({ missions: [{ mission_id: "m-7" }] }), `${name} 2xx body`);
  }
});

// --- non-2xx response ---
test("every fetchJson copy throws an Error with status and the parsed body on non-2xx", async () => {
  for (const name of Object.keys(COPIES)) {
    const { fn } = runCopy(name, "/made-up/queue", undefined,
      { ok: false, status: 409, statusText: "Conflict", body: { error: { message: "plan changed" } } });
    await assert.rejects(() => fn("/made-up/queue"), (err) => {
      assert.strictEqual(err.status, 409, `${name} err.status`);
      assert.strictEqual(JSON.stringify(err.body), JSON.stringify({ error: { message: "plan changed" } }), `${name} err.body`);
      return true;
    });
  }
});

test("board_dnd fetchJson error text is body.error raw: a string stays, an object becomes [object Object]", async () => {
  const dnd = runCopy("board_dnd", "/made-up/queue", undefined,
    { ok: false, status: 409, body: { error: "dnd says no" } });
  await assert.rejects(() => dnd.fn("/made-up/queue"), (err) => {
    assert.strictEqual(err.message, "dnd says no");
    return true;
  });

  const objectError = runCopy("board_dnd", "/made-up/queue", undefined,
    { ok: false, status: 409, body: { error: { message: "nested" } } });
  await assert.rejects(() => objectError.fn("/made-up/queue"), (err) => {
    // difference pinned: other copies use body.error.message; dnd stringifies the object
    assert.strictEqual(err.message, "[object Object]");
    assert.strictEqual(JSON.stringify(err.body), JSON.stringify({ error: { message: "nested" } }));
    return true;
  });
});

test("copies with body.error.message text throw it, others fall back to HTTP <status>", async () => {
  for (const name of ["board_actions", "board_history_ui", "board_mission",
    "board_mission_save", "board_plan_edit", "board_ready_ui"]) {
    const { fn } = runCopy(name, "/made-up/queue", undefined,
      { ok: false, status: 500, body: { error: { message: "server broke" } } });
    await assert.rejects(() => fn("/made-up/queue"), (err) => {
      assert.strictEqual(err.message, "server broke");
      return true;
    });
  }
  // no error field at all -> the plain status fallback
  for (const name of Object.keys(COPIES)) {
    const { fn } = runCopy(name, "/made-up/queue", undefined,
      { ok: false, status: 503, body: {} });
    await assert.rejects(() => fn("/made-up/queue"), (err) => {
      assert.strictEqual(err.message, "HTTP 503");
      return true;
    });
  }
});

// --- a body that is not JSON ---
test("every fetchJson copy swallows a non-JSON body into {} on 2xx and on error", async () => {
  for (const name of Object.keys(COPIES)) {
    const ok = runCopy(name, "/made-up/queue", undefined,
      { ok: true, status: 200, jsonFails: true });
    assert.strictEqual(JSON.stringify(await ok.fn("/made-up/queue")), "{}", `${name} non-JSON 2xx`);

    const bad = runCopy(name, "/made-up/queue", undefined,
      { ok: false, status: 500, jsonFails: true });
    await assert.rejects(() => bad.fn("/made-up/queue"), (err) => {
      assert.strictEqual(err.message, "HTTP 500");
      assert.strictEqual(JSON.stringify(err.body), "{}");
      return true;
    });
  }
});

// --- no timer: the helper awaits fetch and res.json() directly ---
test("no fetchJson copy uses setTimeout or AbortController", () => {
  for (const name of Object.keys(COPIES)) {
    assert.ok(!COPIES[name].source.includes("setTimeout"), `${name} has no setTimeout`);
    assert.ok(!COPIES[name].source.includes("AbortController"), `${name} has no AbortController`);
  }
});

// --- the extraction: one definition, one kept exception ---
function definesFetchJson(file) {
  return /async function fetchJson\(/.test(fs.readFileSync(path.join(SRC, file), "utf8"));
}

test("only board_actions.js and board_dnd.js define fetchJson, the rest bind the shared one", () => {
  for (const file of BOARD_FILES) {
    const keepsOwn = file === "board_actions.js" || file === "board_dnd.js";
    assert.strictEqual(definesFetchJson(file), keepsOwn, `${file} defines a fetchJson body`);
  }
  for (const file of BOARD_FILES.filter((f) => f !== "board_actions.js" && f !== "board_dnd.js")) {
    const text = fs.readFileSync(path.join(SRC, file), "utf8");
    assert.ok(text.includes('require("./board_actions.js")).fetchJson;'),
      `${file} binds the shared fetchJson from board_actions.js`);
  }
});

// --- the shape differences stated explicitly ---
test("shape differences between the shared helper and the kept copy are stated", () => {
  assert.ok(SHARED_SOURCE.includes("url, options"), "only the shared helper accepts options");
  assert.ok(/\? body\.error :/.test(COPIES.board_dnd.source), "board_dnd keeps body.error as the error text");
  assert.ok(!/\? body\.error :/.test(SHARED_SOURCE), "the shared helper uses body.error.message");
});
