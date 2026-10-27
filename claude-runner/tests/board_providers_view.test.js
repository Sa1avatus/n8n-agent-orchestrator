// Node test for board_providers_view.js (providers page).
// Run: node tests/board_providers_view.test.js
//
// Proves:
//   1. open() renders the list (name, base URL, model count, key set_at) and the
//      import proposal;
//   2. a key value in the list payload is never rendered;
//   3. Create posts {name, ...fields} to /api/board/providers with the
//      X-AHAWR-Dashboard: 1 header;
//   4. Test asks for confirmation first: a declined confirm sends nothing, an
//      accepted one posts {model} to /api/board/providers/{name}/test with the header;
//   5. a save version conflict (409) goes through showNotice and to the page's
//      notice line.

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
const I18N_JS = path.join(SRC, "board_i18n.js");
const PROVIDERS_JS = path.join(SRC, "board_providers.js");
const FORM_JS = path.join(SRC, "board_providers_form.js");
const VIEW_JS = path.join(SRC, "board_providers_view.js");
const SECRET = "sk-LEAK-CHECK";

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed`);
}

function makeNode(tag) {
  const node = { tag, children: [], handlers: {}, value: "", checked: false,
    appendChild(c) { node.children.push(c); }, addEventListener(type, fn) { node.handlers[type] = fn; } };
  let text = "";
  Object.defineProperty(node, "textContent", {
    get: () => text,
    set: (v) => { text = String(v); node.children.length = 0; },
  });
  if (tag === "select") {
    const pick = () => node.children.find((o) => o.selected) || node.children[0] || { value: "" };
    Object.defineProperty(node, "value", {
      get: () => pick().value,
      set: (v) => node.children.forEach((o) => { o.selected = o.value === v; }),
    });
  }
  return node;
}

function allNodes(root) {
  const out = [];
  const walk = (n) => { out.push(n); n.children.forEach(walk); };
  walk(root);
  return out;
}

function allText(root) {
  return allNodes(root).map((n) => n.textContent).join("\n");
}

function buttons(root, label) {
  return allNodes(root).filter((n) => n.tag === "button" && n.textContent === label);
}

function reply(payload, ok = true, status = ok ? 200 : 400) {
  return Promise.resolve({ ok, status, json: async () => payload });
}

const PROVIDERS = [{
  name: "alpha", label: "alpha", api_type: "anthropic", base_url: "https://a.example",
  enabled: true, notes: "", version: "v+1",
  models: [{ model: "m1" }, { model: "m2" }], key: SECRET,
  key_state: { has_key: true, set_at: "2026-10-01T00:00:00Z" },
}];
const TEMPLATES = [{ name: "deepseek", base_url: "https://api.deepseek.com/v1", api_type: "openai-chat",
  hint: "vendor OpenAI-compatible endpoint; auth not checked", verified: false }];
const PROPOSALS = [
  { name: "beta", has_key: true, exists: false,
    fields: { label: "beta", api_type: "anthropic", base_url: "https://b.example" } },
  { name: "alpha", has_key: false, exists: true,
    fields: { label: "alpha", api_type: "anthropic", base_url: "https://a.example" } },
];

function load() {
  const calls = [];
  const env = { answer: true };
  const board = makeNode("div");
  const document = {
    createElement: makeNode,
    getElementById: (id) => (id === "board" ? board : null),
    body: makeNode("body"),
  };
  const fetch = (url, opts = {}) => {
    const method = opts.method || "GET";
    calls.push({ url, method, headers: opts.headers, body: opts.body ? JSON.parse(opts.body) : undefined });
    if (method === "GET" && url === "/api/board/providers") return reply({ providers: PROVIDERS });
    if (method === "GET" && url === "/api/board/providers/import-from-env") {
      return reply({ dry_run: true, proposals: PROPOSALS });
    }
    if (method === "GET" && url === "/api/board/providers/templates") {
      return reply({ templates: TEMPLATES });
    }
    if (method === "POST" && url === "/api/board/providers") return reply({ provider: PROVIDERS[0] });
    if (method === "PUT" && url === "/api/board/providers/alpha" && opts.body && JSON.parse(opts.body).dry_run) {
      return reply({ error: { message: "conflict" } }, false, 409);
    }
    if (method === "GET" && url === "/api/board/providers/alpha/available-models") {
      return reply({ status: 200, models: [
        { id: "m1", name: "m1", free: null, price_in: null, price_out: null },
        { id: "vendor/free-one:free", name: "F", free: true, price_in: 0, price_out: 0 },
        { id: "vendor/paid", name: "P", free: false, price_in: 1, price_out: 2 },
      ] });
    }
    if (method === "DELETE" && url.startsWith("/api/board/providers/alpha?")) {
      if (globalThis.__deleteRefused) return reply({ error: { message: "provider 'alpha' is used by a role" } }, false, 409);
      return reply({ deleted: "alpha" });
    }
    if (method === "POST" && url === "/api/board/providers/alpha/models") return reply({ provider: PROVIDERS[0] });
    if (method === "POST" && url.endsWith("/test")) return reply({ status: 200, latency_ms: 42 });
    return reply({ error: { message: "unexpected " + method + " " + url } }, false);
  };
  const notices = [];
  const sandbox = {
    console,
    URL,
    localStorage: { getItem: () => null, setItem: () => {} },
    document,
    fetch,
    confirm: () => env.answer,
    BoardDnd: { showNotice: (text) => notices.push(text) },
    module: { exports: {} },
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(I18N_JS, "utf8"), sandbox, { filename: I18N_JS });
  vm.runInContext(fs.readFileSync(PROVIDERS_JS, "utf8"), sandbox, { filename: PROVIDERS_JS });
  sandbox.BoardProviders = sandbox.module.exports;
  vm.runInContext(fs.readFileSync(FORM_JS, "utf8"), sandbox, { filename: FORM_JS });
  sandbox.BoardProvidersForm = sandbox.module.exports;
  vm.runInContext(fs.readFileSync(VIEW_JS, "utf8"), sandbox, { filename: VIEW_JS });
  return { view: sandbox.module.exports, board, calls, env, notices };
}

async function main() {
  {
    const { view, board } = load();
    await view.open();
    const text = allText(board);
    check("name and base URL rendered", text.includes("alpha") && text.includes("https://a.example"));
    check("key set_at rendered", text.includes("2026-10-01T00:00:00Z"));
    check("the first provider is selected and its form is open", text.includes("Provider alpha"));
    check("its models are listed", text.includes("m1") && text.includes("m2"));
    check("the board shows only the providers page", board.className === "provopen");
    check("import proposal rendered", text.includes("https://b.example"));
    check("key value never rendered", !text.includes(SECRET));
  }

  {
    const { view, board, calls } = load();
    await view.open();
    const create = buttons(board, "Create").find((b) => !b.disabled);
    await create.handlers.click();
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/board/providers");
    check("create posts the proposal", post && post.body.name === "beta" && post.body.base_url === "https://b.example");
    check("create carries the dashboard header", post && post.headers["X-AHAWR-Dashboard"] === "1");
  }

  {
    const { view, board, calls, env } = load();
    await view.open();
    const checks = allNodes(board).find((n) => String(n.className).startsWith("bvprov-checks"));
    const select = allNodes(checks).find((n) => n.tag === "select");
    select.value = "m2";
    env.answer = false;
    await buttons(board, "Test")[0].handlers.click();
    check("declined confirm sends nothing", !calls.some((c) => c.url.endsWith("/test")));
    env.answer = true;
    await buttons(board, "Test")[0].handlers.click();
    const test = calls.find((c) => c.url.endsWith("/test"));
    check("test posts the chosen model", test && test.url === "/api/board/providers/alpha/test" && test.body.model === "m2");
    check("test carries the dashboard header", test && test.headers["X-AHAWR-Dashboard"] === "1");
  }

  {
    const { view, board, calls, env, notices } = load();
    env.answer = true;
    await view.open();
    await buttons(board, "Save")[0].handlers.click();
    check("conflict reaches showNotice", notices.length === 1 && notices[0].includes("Version conflict"));
    check("the conflict is in the page notice line", allText(board).includes("Version conflict"));
    check("no confirmed write after the conflict",
      !calls.some((c) => c.method === "PUT" && c.body && !c.body.dry_run));
  }
  {
    const { view, board } = load();
    await view.open();
    await buttons(board, "New provider")[0].handlers.click();
    const text = allText(board);
    check("a new provider offers the template select", text.includes("Add from template"));
    check("a new provider has no Test button", buttons(board, "Test").length === 0);
  }

  {
    const { view, board } = load();
    await view.open();
    const item = allNodes(board).find((n) => n.tag === "button" && String(n.className).startsWith("bvprov-item"));
    check("the provider list item shows the key state", allText(item).includes("key set"));
    const back = buttons(board, "Back to missions")[0];
    back.handlers.click();
    check("closing the page shows the board again", board.className === "");
  }
  {
    const { view, board, calls } = load();
    await view.open();
    await buttons(board, "Fetch models")[0].handlers.click();
    const listed = () => allNodes(board).filter((n) => String(n.className) === "bvprov-listed");
    check("both new models are listed", listed().length === 2);
    const freeLabel = allNodes(board).find((n) => n.tag === "label" && allText(n).includes("Free only"));
    const freeBox = freeLabel && freeLabel.children[0];
    check("a free-only switch exists", Boolean(freeBox));
    freeBox.checked = true;
    freeBox.handlers.change();
    check("free only keeps the free model", listed().length === 1 && allText(listed()[0]).includes("vendor/free-one:free"));
    await buttons(board, "Select shown")[0].handlers.click();
    await buttons(board, "Add selected (1)")[0].handlers.click();
    const post = calls.find((c) => c.method === "POST" && c.url === "/api/board/providers/alpha/models");
    check("the picked model is posted with its prices",
      post && post.body.model === "vendor/free-one:free" && post.body.price_input_per_mtok_usd === 0);
    check("the add carries the dashboard header", post && post.headers["X-AHAWR-Dashboard"] === "1");
  }
  {
    const { view, board, calls, env } = load();
    await view.open();
    env.answer = false;
    await buttons(board, "Delete provider")[0].handlers.click();
    check("a declined delete sends nothing", !calls.some((c) => c.method === "DELETE"));
    env.answer = true;
    await buttons(board, "Delete provider")[0].handlers.click();
    const del = calls.find((c) => c.method === "DELETE");
    check("delete carries the encoded version in the query",
      del && del.url === "/api/board/providers/alpha?expected_version=v%2B1");
    check("delete carries the dashboard header", del && del.headers["X-AHAWR-Dashboard"] === "1");
  }
  {
    const { view, board, env, notices } = load();
    await view.open();
    env.answer = true;
    globalThis.__deleteRefused = true;
    await buttons(board, "Delete provider")[0].handlers.click();
    globalThis.__deleteRefused = false;
    check("a refused delete is explained on the providers page", allText(board).includes("is used by a role"));
    check("the refusal also reaches showNotice", notices.some((n) => n.includes("is used by a role")));
  }
  console.log("board_providers_view tests passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
