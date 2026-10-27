// Node test for board_providers_form.js (provider add/edit form, key field, models table).
// Run: node tests/board_providers_form.test.js
//
// Proves:
//   1. the pure helpers: a blank key gives no body, prices parse or stay as text,
//      capability JSON is parsed or reported, the worker and LiteLLM notes, the changed fields;
//   2. the key field is write-only: the key is sent once to PUT .../key, the input is
//      cleared after the request is sent, and a key echoed in the response is never shown;
//   3. a save with a version conflict (409) goes to notify and reloads, and nothing is written.

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
const PROVIDERS_JS = path.join(SRC, "board_providers.js");
const FORM_JS = path.join(SRC, "board_providers_form.js");
const SECRET = "sk-SECRET-1234";

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed`);
}

function makeNode(tag) {
  const node = { tag, children: [], handlers: {}, appendChild(c) { node.children.push(c); },
    addEventListener(type, fn) { node.handlers[type] = fn; } };
  let text = "";
  Object.defineProperty(node, "textContent", {
    get: () => text,
    set: (v) => { text = String(v); node.children.length = 0; },
  });
  return node;
}

function allNodes(root) {
  const out = [];
  const walk = (n) => { out.push(n); n.children.forEach(walk); };
  walk(root);
  return out;
}

function buttons(root, label) {
  return allNodes(root).filter((n) => n.tag === "button" && n.textContent === label);
}

// A browser select takes its value from the selected option; this fake does the same.
function selectValue(node) {
  const pick = () => node.children.find((o) => o.selected) || node.children[0] || { value: "" };
  Object.defineProperty(node, "value", {
    get: () => pick().value,
    set: (v) => node.children.forEach((o) => { o.selected = o.value === v; }),
  });
}

function reply(payload, ok = true, status = ok ? 200 : 409) {
  return Promise.resolve({ ok, status, payload });
}

// Like providerWrite in board_providers_view.js: a failed response throws with its status.
function unwrapped(response) {
  if (!response.ok) {
    const err = new Error(response.payload.error.message);
    err.status = response.status;
    throw err;
  }
  return response.payload;
}

const L = (key, en, params) => en.replace(/\{(\w+)\}/g, (m, name) =>
  (params && params[name] !== undefined ? String(params[name]) : m));

const PROVIDER = {
  name: "alpha", label: "alpha", api_type: "anthropic", base_url: "https://a.example",
  enabled: true, notes: "", version: "v1",
  key_state: { has_key: false, set_at: null },
  models: [{
    model: "m1", alias: "", version: "mv1", supports_thinking: false,
    price_input_per_mtok_usd: null, price_output_per_mtok_usd: null,
    api_type: null, capability_overrides: null, probe_result: null,
  }],
};

function load() {
  const calls = [];
  const confirms = [];
  const sandbox = {
    console,
    URL,
    confirm: (text) => { confirms.push(text); return sandbox.answer; },
    answer: true,
    calls,
    confirms,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(PROVIDERS_JS, "utf8"), sandbox, { filename: PROVIDERS_JS });
  vm.runInContext(fs.readFileSync(FORM_JS, "utf8"), sandbox, { filename: FORM_JS });
  return sandbox;
}

function uiFor(respond) {
  const notes = [];
  const reloads = [];
  const ui = {
    L,
    el: (tag, props, children) => {
      const node = makeNode(tag);
      Object.assign(node, props);
      for (const child of children) node.appendChild(child);
      if (tag === "select") selectValue(node);
      return node;
    },
    keyText: (keyState) => (keyState.has_key ? "key set" : "no key"),
    write: (method, url, body) => {
      const snapshot = { method, url, body: JSON.parse(JSON.stringify(body)) };
      ui.calls.push(snapshot);
      return respond(method, url, body, ui).then(unwrapped);
    },
    notify: (text) => notes.push(text),
    reload: () => { reloads.push(true); },
    edit: () => {},
    close: () => {},
    calls: [],
  };
  return { ui, notes, reloads };
}

async function main() {
  const sandbox = load();
  const F = sandbox.BoardProvidersForm;
  const P = sandbox.BoardProviders;

  // --- 1. pure helpers ---
  {
    check("blank key gives no body", F.keyBody("   ") === null);
    check("key body is the key", JSON.stringify(F.keyBody(SECRET)) === JSON.stringify({ key: SECRET }));
    check("price parses", F.numberOrText("1.5") === 1.5);
    check("blank price is null", F.numberOrText("") === null);
    check("bad price stays text for the server", F.numberOrText("abc") === "abc");
    const bad = F.modelFieldsFrom({ alias: "", price_input: "", price_output: "", supports_thinking: false,
      api_type: "", capabilities: "{not json" });
    check("bad capability JSON is reported", typeof bad.error === "string" && !bad.fields);
    const good = F.modelFieldsFrom({ alias: "a", price_input: "2", price_output: "", supports_thinking: true,
      api_type: "", capabilities: '{"tools": false}' });
    check("capability JSON parsed", good.fields.capability_overrides.tools === false);
    check("blank API type is inherit", good.fields.api_type === null);
    const plain = F.modelNotes(PROVIDER, PROVIDER.models[0]);
    check("anthropic model: no worker warning", plain.noWorker === false);
    check("anthropic model: synced", plain.notSynced === false);
    const litellm = F.modelNotes({ ...PROVIDER, api_type: "openai-chat" }, PROVIDER.models[0]);
    check("LiteLLM route: not synced", litellm.notSynced === true);
    const noTools = F.modelNotes(PROVIDER, { ...PROVIDER.models[0], capability_overrides: { tools: false } });
    check("tools false: not usable as a Worker", noTools.noWorker === true);
    const probed = F.modelNotes(PROVIDER, { ...PROVIDER.models[0], probe_result: { worker_warning: "no_tools" } });
    check("probe warning: not usable as a Worker", probed.noWorker === true);
    const changed = F.modelChanges(PROVIDER.models[0], { alias: "b", supports_thinking: false });
    check("only the changed alias is listed", Object.keys(changed).join() === "alias");
    check("diff shows before and after", F.diffText(changed) === 'alias: "" -> "b"');
    check("types known", P.getApiType("anthropic").id === "anthropic");
    const tpl = F.templateFields({ name: "ollama", base_url: "http://localhost:11434/v1",
      api_type: "openai-chat", hint: "local server", verified: false });
    check("template fields carry the prefilled values", JSON.stringify(tpl) === JSON.stringify({
      name: "ollama", label: "ollama", base_url: "http://localhost:11434/v1", api_type: "openai-chat",
    }));
  }

  // --- 2. key field is write-only ---
  {
    const { ui, notes, reloads } = uiFor((method, url) => {
      if (method === "PUT" && url.endsWith("/key")) {
        return reply({ key_state: { has_key: true, set_at: "2026-10-26T00:00:00Z" }, key: SECRET });
      }
      return reply({ error: { message: "unexpected" } }, false);
    });
    const root = F.build(PROVIDER, ui);
    const input = allNodes(root).find((n) => n.tag === "input" && n.type === "password");
    check("key input is a password field", input !== undefined);
    input.value = SECRET;
    let valueWhenSent = null;
    const originalWrite = ui.write;
    ui.write = (method, url, body) => {
      valueWhenSent = input.value;
      return originalWrite(method, url, body);
    };
    await buttons(root, "Save key")[0].handlers.click();
    check("key sent once to the key route", ui.calls.length === 1 && ui.calls[0].url.endsWith("/alpha/key"));
    check("key body carries the key", ui.calls[0].body.key === SECRET);
    check("key still in the input when the request is sent", valueWhenSent === SECRET);
    check("key input cleared after send", input.value === "");
    check("reload after saving the key", reloads.length === 1);
    const shown = allNodes(root).map((n) => n.textContent).join("\n") + notes.join("\n");
    check("echoed key never shown", !shown.includes(SECRET));
  }

  // --- 3. version conflict goes to notify and reloads ---
  {
    const { ui, notes, reloads } = uiFor((method, url, body) => {
      if (method === "PUT" && body.dry_run) return reply({ error: { message: "conflict" } }, false, 409);
      return reply({ error: { message: "unexpected" } }, false);
    });
    const root = F.build(PROVIDER, ui);
    const selects = allNodes(root).filter((n) => n.tag === "select");
    selects[0].value = "anthropic";
    await buttons(root, "Save")[0].handlers.click();
    check("conflict reported through notify", notes.length === 1 && notes[0].includes("Version conflict"));
    check("conflict reloads the page", reloads.length === 1);
    check("nothing written after the conflict", ui.calls.every((c) => c.body.dry_run === true));
    check("no confirm asked on a conflict", sandbox.confirms.length === 0);
  }

  // --- 4. add from template prefills the new-provider form ---
  {
    const templates = [
      { name: "deepseek", base_url: "https://api.deepseek.com/v1", api_type: "openai-chat",
        hint: "vendor OpenAI-compatible endpoint; auth not checked", verified: false },
      { name: "anthropic-direct", base_url: "https://api.anthropic.com", api_type: "anthropic",
        hint: "verified by the runner", verified: true },
    ];
    const { ui } = uiFor(() => reply({ error: { message: "unexpected" } }, false));
    ui.templates = templates;
    const root = F.build(null, ui);
    const inputOf = (labelText) => {
      const lab = allNodes(root).find((n) => n.tag === "label" &&
        n.children.some((c) => c.tag === "span" && c.textContent === labelText));
      return lab.children.find((n) => n.tag === "input");
    };
    const inputs = {
      name: inputOf("Name"),
      label: inputOf("Label"),
      api_type: allNodes(root).find((n) => n.tag === "select" && !n.children.some((o) => o.value === "")),
      base_url: inputOf("Base URL"),
    };
    const hint = allNodes(root).find((n) => n.tag === "p" && n.className === "bvprov-template-hint");
    const apply = buttons(root, "Apply")[0];
    const select = allNodes(root).find((n) => n.tag === "select" && n.children[0].value === "");
    check("the template select has one option per template", select.children.length === 3);
    select.value = "deepseek";
    await apply.handlers.click();
    check("apply prefills the name", inputs.name.value === "deepseek");
    check("apply prefills the label", inputs.label.value === "deepseek");
    check("apply prefills the base URL", inputs.base_url.value === "https://api.deepseek.com/v1");
    check("apply prefills the API type", inputs.api_type.value === "openai-chat");
    check("the LiteLLM note is shown for the type", allNodes(root).some((n) => n.textContent.includes("LiteLLM")));
    check("the hint is shown", hint.textContent.includes("vendor OpenAI-compatible endpoint"));
    check("the template is marked unverified", hint.textContent.includes("unverified"));
    select.value = "anthropic-direct";
    await apply.handlers.click();
    check("a verified template shows no unverified mark", !hint.textContent.includes("unverified"));
    check("the extra-fields note replaces the LiteLLM note",
      allNodes(root).some((n) => n.textContent.includes("anthropic-version")));
    check("no write on applying a template", ui.calls.length === 0);
  }

  console.log("board_providers_form.test.js: all checks passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
