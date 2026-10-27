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
  enabled: true, notes: "",
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
    check("model count rendered", allNodes(board).some((n) => n.textContent === "2"));
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
    const select = allNodes(board).find((n) => n.tag === "select");
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
    await buttons(board, "Edit")[0].handlers.click();
    await buttons(board, "Save")[0].handlers.click();
    check("conflict reaches showNotice", notices.length === 1 && notices[0].includes("Version conflict"));
    check("the conflict is in the page notice line", allText(board).includes("Version conflict"));
    check("no confirmed write after the conflict",
      !calls.some((c) => c.method === "PUT" && c.body && !c.body.dry_run));
  }
  console.log("board_providers_view tests passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
