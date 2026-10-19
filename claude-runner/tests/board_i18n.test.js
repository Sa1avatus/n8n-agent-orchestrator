// Node test for board_i18n.js (t(), languages, persistence, fallback).
// Run: node claude-runner/tests/board_i18n.test.js
//
// Proves:
//   1. t() fills {name} placeholders from params and keeps unknown ones;
//   2. the fallback chain: current language, then English, then the bundled
//      table, then the key itself;
//   3. the language is read from localStorage on load and written on
//      setLanguage, and setLanguage notifies onChange listeners;
//   4. load() fetches /api/board/i18n once, takes languages and strings from
//      it, and keeps the bundled English table when the request fails;
//   5. the bundled table equals board_i18n_en.json, and the file defines only
//      BoardI18n and t globally.

"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "src", "claude_runner");
const MODULE = path.join(SRC, "board_i18n.js");
const EN_JSON = JSON.parse(fs.readFileSync(path.join(SRC, "board_i18n_en.json"), "utf8"));

function check(name, cond) {
  if (!cond) throw new Error(`${name} failed: ` + JSON.stringify(cond));
}

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: (k) => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => data.set(k, String(v)),
    data,
  };
}

// Loads a fresh copy of the module with the given storage and API outcome.
function loadBoard({ storage = memoryStorage(), api = null } = {}) {
  const calls = [];
  globalThis.localStorage = storage;
  globalThis.getJSON = (url) => {
    calls.push(url);
    if (api instanceof Error) return Promise.reject(api);
    return Promise.resolve(api);
  };
  delete require.cache[MODULE];
  require(MODULE);
  return { I: globalThis.BoardI18n, calls, storage };
}

const SERVER = {
  languages: [
    { code: "en", name: "English", enabled: true },
    { code: "fr", name: "Francais", enabled: true },
    { code: "de", name: "Deutsch", enabled: false },
  ],
  strings: {
    en: { "only.server": "server English", "shared": "server shared" },
    fr: { "shared": "fr shared", "greet": "bonjour {name}" },
  },
};

async function main() {
  // --- 1. parameters ---
  {
    const { I } = loadBoard();
    check("param substituted", I.t("theme", { mode: "dark" }) === "Theme: dark");
    check("two params", I.t("of", { a: 1, b: 2 }) === "1 out of 2");
    check("missing param kept", I.t("of", { a: 1 }) === "1 out of {b}");
    check("zero param", I.t("hours", { n: 0 }) === "0 h");
    check("no params", I.t("app.title") === "Missions");
    check("global t", globalThis.t("app.title") === "Missions");
  }

  // --- 2. fallback chain ---
  {
    const { I } = loadBoard({ api: SERVER });
    await I.load();
    I.setLanguage("fr");
    check("current language wins", I.t("shared") === "fr shared");
    check("param in current language", I.t("greet", { name: "Ann" }) === "bonjour Ann");
    check("falls back to English", I.t("only.server") === "server English");
    check("falls back to bundled English", I.t("app.title") === "Missions");
    check("unknown key returns the key", I.t("no.such.key") === "no.such.key");
    I.setLanguage("de");
    check("language without strings uses English", I.t("shared") === "server shared");
  }

  // --- 3. persistence ---
  {
    const { I, storage } = loadBoard();
    check("default language", I.getLanguage() === "en");
    I.setLanguage("fr");
    check("written to storage", storage.data.get("board.lang") === "fr");
    const again = loadBoard({ storage });
    check("read from storage on load", again.I.getLanguage() === "fr");
    const seen = [];
    again.I.onChange((code) => seen.push(code));
    again.I.setLanguage("de");
    check("listener notified", seen.length === 1 && seen[0] === "de");
  }

  // --- 4. load: one request, and the bundled table on failure ---
  {
    const { I, calls } = loadBoard({ api: SERVER });
    const first = I.load();
    const second = I.load();
    check("same promise", first === second);
    await first;
    await I.load();
    check("single request", calls.length === 1 && calls[0] === "/api/board/i18n");
    check("enabled languages only",
      I.languages.map((l) => l.code).join(",") === "en,fr");
  }
  {
    const { I, calls } = loadBoard({ api: new Error("HTTP 503") });
    const origWarn = console.warn;
    console.warn = () => {};
    try {
      await I.load();
    } finally {
      console.warn = origWarn;
    }
    check("failed request made once", calls.length === 1);
    check("bundled English after failure", I.t("app.title") === "Missions");
    check("languages stay English-only", I.languages.map((l) => l.code).join(",") === "en");
  }

  // --- 5. bundled table and globals ---
  {
    const { I } = loadBoard({ api: new Error("offline") });
    const origWarn = console.warn;
    console.warn = () => {};
    try {
      await I.load();
    } finally {
      console.warn = origWarn;
    }
    const keys = Object.keys(EN_JSON);
    check("bundled table matches the JSON file",
      keys.length > 0 && keys.every((k) => I.t(k) === EN_JSON[k]));
  }
  {
    const before = new Set(["console", "localStorage", "getJSON"]);
    const sandbox = {
      console, localStorage: memoryStorage(), getJSON: () => Promise.reject(new Error("x")),
    };
    vm.createContext(sandbox);
    vm.runInContext(fs.readFileSync(MODULE, "utf8"), sandbox, { filename: MODULE });
    const added = Object.keys(sandbox).filter((k) => !before.has(k)).sort();
    check("only BoardI18n and t are global", added.join(",") === "BoardI18n,t");
  }

  console.log("board_i18n.test.js: all checks passed");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
