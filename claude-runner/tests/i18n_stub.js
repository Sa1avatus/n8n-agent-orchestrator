"use strict";
// Node tests load board modules without board_i18n.js; this stub gives them t() returning English.
const fs = require("fs");
const path = require("path");

if (typeof globalThis.t !== "function") {
  const table = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "src", "claude_runner", "board_i18n_en.json"), "utf8")
  );
  globalThis.t = (key, params) => {
    let s = Object.prototype.hasOwnProperty.call(table, key) ? table[key] : key;
    if (params) for (const [k, v] of Object.entries(params)) s = s.split("{" + k + "}").join(String(v));
    return s;
  };
}
