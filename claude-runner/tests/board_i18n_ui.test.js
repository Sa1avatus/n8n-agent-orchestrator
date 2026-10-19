// Node test for board_i18n_ui.js (translation editor).
// Run: node --test claude-runner/tests/board_i18n_ui.test.js
//
// Proves:
//   1. an input change saves with exactly PUT /api/board/i18n/{lang}/{key},
//      carrying the X-AHAWR-Dashboard: 1 header and a JSON {text} body;
//   2. the counter renders "Missing: {n} of {total}." (tr.saved);
//   3. a hostile value stays in the input's value property and never appears
//      as textContent (no innerHTML anywhere in the source);
//   4. a failed PUT puts the server message into the error line;
//   5. add-language posts {code, name} to /api/board/i18n/languages and the
//      new language is selected in the language select;
//   6. BoardI18n.t returns the saved text after the save, and setString
//      notifies the onChange listeners.

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
const I18N_JS = path.join(SRC, "board_i18n.js");
const UI_JS = path.join(SRC, "board_i18n_ui.js");

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    data,
  };
}

// A minimal document: elements keep properties (value, textContent, ...)
// and the handlers registered via addEventListener; no innerHTML exists.
function makeDoc() {
  const make = (tag) => {
    const node = {
      tag,
      children: [],
      handlers: {},
      value: "",
      placeholder: "",
      className: "",
      appendChild(child) { node.children.push(child); },
      addEventListener(type, fn) { node.handlers[type] = fn; },
    };
    // Setting textContent replaces all children (as in the real DOM), so a
    // re-render does not accumulate the old tree.
    let text = "";
    Object.defineProperty(node, "textContent", {
      get: () => text,
      set: (v) => { text = String(v); node.children.length = 0; },
    });
    // Real DOM elements take checked/selected via setters; the mock accepts
    // them as plain properties so Object.assign(node, props) works.
    let checked = false;
    let selected = false;
    Object.defineProperty(node, "checked", { get: () => checked, set: (v) => { checked = !!v; } });
    Object.defineProperty(node, "selected", { get: () => selected, set: (v) => { selected = !!v; } });
    return node;
  };
  const board = make("div");
  return {
    board,
    document: {
      createElement: make,
      getElementById: (id) => (id === "board" ? board : null),
      addEventListener: () => {},
      body: make("body"),
    },
  };
}

// Loads a fresh pair of modules (board_i18n.js, then board_i18n_ui.js) into
// a sandbox with a recording fetch.
function loadUi(fetchImpl) {
  const calls = [];
  const { board, document } = makeDoc();
  const sandbox = {
    console,
    localStorage: memoryStorage(),
    document,
    globalThis: undefined,
    fetch: (url, opts) => {
      calls.push({ url, opts });
      return fetchImpl(url, opts);
    },
  };
  vm.createContext(sandbox);
  sandbox.globalThis = sandbox;
  vm.runInContext(fs.readFileSync(I18N_JS, "utf8"), sandbox, { filename: I18N_JS });
  vm.runInContext(fs.readFileSync(UI_JS, "utf8"), sandbox, { filename: UI_JS });
  return { I: sandbox.globalThis.BoardI18n, ui: sandbox.globalThis.BoardI18nUI, calls, board };
}

function allNodes(root) {
  const out = [];
  const walk = (n) => { out.push(n); n.children.forEach(walk); };
  walk(root);
  return out;
}

function find(root, pred) {
  return allNodes(root).find(pred);
}

// A fresh server view for each section; the UI keeps state.strings as a
// reference, so a save in one section must not leak into the next.
function serverView() {
  return {
    languages: [
      { code: "en", name: "English", enabled: true },
      { code: "fr", name: "Francais", enabled: true },
    ],
    strings: {
      en: { greet: "Hello", hello: "Hi", bye: "Bye" },
      fr: { greet: "bonjour" },
    },
  };
}

async function main() {
  // --- 1+2+3+6: save on change, counter, hostile value, t() after save ---
  {
    const { I, ui, calls, board } = loadUi((url) =>
      Promise.resolve({ ok: true, json: async () => (url === "/api/board/i18n" ? serverView() : {}) }));
    await ui.open();

    const section = board.children[0];
    const counter = find(section, (n) => n.tag === "p" && n.className === "mono");
    check("the counter renders tr.saved",
      counter.textContent === "Saved to the database (demo: in memory). Missing: 2 of 3.");

    // The input of the greet row: locate the row by its key cell, then its input.
    const greetRow = find(section, (n) =>
      n.tag === "tr" && n.children.some((c) => c.tag === "td" && c.textContent === "greet"));
    const greetInput = greetRow.children.find(
      (c) => c.tag === "td" && c.children.some((i) => i.tag === "input"));
    check("input for the fr key exists", greetInput !== undefined);
    const seen = [];
    I.onChange((code) => seen.push(code));

    const hostile = "<img src=x onerror=1>";
    greetInput.children[0].value = hostile;
    greetInput.children[0].handlers.change();
    await new Promise((r) => setTimeout(r, 0));

    const put = calls.find((c) => c.url === "/api/board/i18n/fr/greet");
    check("PUT request recorded", put !== undefined);
    check("PUT method", put.opts.method === "PUT");
    check("PUT headers carry the dashboard header",
      put.opts.headers["X-AHAWR-Dashboard"] === "1" &&
      put.opts.headers["Content-Type"] === "application/json");
    check("PUT body is JSON {text}", put.opts.body === JSON.stringify({ text: hostile }));

    check("the saved hostile text is in the input's value property",
      find(section, (n) => n.tag === "input" && n.value === hostile) !== undefined);
    check("the hostile text never appears as textContent",
      allNodes(section).every((n) => !String(n.textContent).includes(hostile)));

    check("setString notifies the onChange listeners", seen.length === 1 && seen[0] === "fr");
    I.setLanguage("fr");
    check("BoardI18n.t returns the saved text after the save", I.t("greet") === hostile);
    check("a key without a saved translation is unchanged", I.t("hello") === "hello");

    const helloRow = find(section, (n) => n.tag === "tr" && n.className === "tr-miss");
    check("the untranslated row is still marked missing", helloRow !== undefined);
  }

  // --- 4: error state when the PUT fails ---
  {
    const { ui, calls, board } = loadUi((url) => {
      if (url === "/api/board/i18n") {
        return Promise.resolve({ ok: true, json: async () => serverView() });
      }
      return Promise.resolve({
        ok: false,
        status: 409,
        json: async () => ({ error: { message: "no such key" } }),
      });
    });
    await ui.open();

    const section = board.children[0];
    const input = find(section, (n) => n.tag === "input" && n.value === "bonjour");
    input.value = "changed";
    input.handlers.change();
    await new Promise((r) => setTimeout(r, 0));

    const bad = find(section, (n) => n.tag === "p" && n.className === "bad");
    check("the error line shows the server message", bad !== undefined && bad.textContent === "no such key");
    check("a PUT was attempted", calls.some((c) => c.url === "/api/board/i18n/fr/greet" && c.opts.method === "PUT"));
  }

  // --- 5: add language through POST /api/board/i18n/languages ---
  {
    const { I, ui, calls, board } = loadUi((url) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          url === "/api/board/i18n/languages"
            ? {
                languages: [
                  { code: "en", name: "English", enabled: true },
                  { code: "de", name: "Deutsch", enabled: true },
                ],
                strings: { en: serverView().strings.en, de: {} },
              }
            : serverView(),
      }));
    await ui.open();

    const section = board.children[0];
    const codeInput = find(section, (n) => n.tag === "input" && n.placeholder === "code (e.g. fr)");
    const nameInput = find(section, (n) => n.tag === "input" && n.placeholder === "name (e.g. Français)");
    check("the add-language inputs exist", codeInput !== undefined && nameInput !== undefined);
    const addButton = find(section, (n) => n.tag === "button" && n.textContent === "Add language");
    check("the add-language button exists", addButton !== undefined);

    codeInput.value = " DE ";
    nameInput.value = " Deutsch ";
    addButton.handlers.click();
    await new Promise((r) => setTimeout(r, 0));

    const post = calls.find((c) => c.url === "/api/board/i18n/languages");
    check("POST request recorded", post !== undefined);
    check("POST method", post.opts.method === "POST");
    check("POST body is the trimmed code and name",
      post.opts.body === JSON.stringify({ code: "de", name: "Deutsch" }));
    check("POST headers carry the dashboard header", post.opts.headers["X-AHAWR-Dashboard"] === "1");

    const select = find(section, (n) => n.tag === "select");
    const deOption = select && find(select, (n) => n.tag === "option" && n.value === "de");
    check("the new language is selected", deOption !== undefined && deOption.selected === true);
  }

  // --- 7: the source contains no innerHTML ---
  {
    const text = fs.readFileSync(UI_JS, "utf8");
    check("board_i18n_ui.js has no innerHTML", !text.includes("innerHTML"));
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
