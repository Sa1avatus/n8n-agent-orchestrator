/* board-scope-wrapped */
(function () {
/* Board translation editor (T011): the screen opened from the header
 * Translations button (the boardviews:translations event).
 *
 * open() reads GET /api/board/i18n and renders the editor into #board: a
 * language select, the "only missing" filter, the counter (tr.saved),
 * one text input per English key and the add-language form. A change in a
 * translation input is saved with PUT /api/board/i18n/{lang}/{key}; the saved
 * text is then written into BoardI18n (setString), so the board views show it
 * at once. Writes go through i18nWrite, which carries the dashboard header.
 *
 * Every value is put into the DOM with textContent or the value property.
 */
"use strict";

const state = { languages: [], strings: {}, lang: "en", onlyMissing: false, error: "", root: null };

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.appendChild(child);
  return node;
}

async function unwrap(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((payload.error && payload.error.message) || "HTTP " + res.status);
  }
  return payload;
}

async function readI18n() {
  return unwrap(await fetch("/api/board/i18n", { cache: "no-store" }));
}

async function i18nWrite(method, url, body) {
  return unwrap(await fetch(url, {
    method,
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-AHAWR-Dashboard": "1" },
    body: JSON.stringify(body),
  }));
}

function languagesToTranslate() {
  return state.languages.filter((lang) => lang.code !== "en");
}

function englishKeys() {
  return Object.keys(state.strings.en || {}).sort();
}

function translationOf(lang, key) {
  return (state.strings[lang] || {})[key] || "";
}

function setTranslation(lang, key, text) {
  const table = state.strings[lang] || (state.strings[lang] = {});
  if (text) table[key] = text;
  else delete table[key];
}

function saveText(input, key) {
  const lang = state.lang;
  const text = input.value.trim() ? input.value : "";
  const url = "/api/board/i18n/" + encodeURIComponent(lang) + "/" + encodeURIComponent(key);
  return i18nWrite("PUT", url, { text }).then(
    () => {
      setTranslation(lang, key, text);
      BoardI18n.setString(lang, key, text);
      state.error = "";
      render();
    },
    (err) => {
      state.error = err.message;
      render();
    },
  );
}

function addLanguage(code, name) {
  const body = { code: code.trim().toLowerCase(), name: name.trim() };
  return i18nWrite("POST", "/api/board/i18n/languages", body).then(
    (view) => {
      state.languages = view.languages;
      state.strings = view.strings;
      state.lang = body.code;
      state.error = "";
      render();
    },
    (err) => {
      state.error = err.message;
      render();
    },
  );
}

function render() {
  const keys = englishKeys();
  const missing = keys.filter((key) => !translationOf(state.lang, key));
  const shown = state.onlyMissing ? missing : keys;

  const langSelect = el("select", {}, languagesToTranslate().map((lang) =>
    el("option", { value: lang.code, textContent: lang.name, selected: lang.code === state.lang }, [])));
  langSelect.addEventListener("change", () => {
    state.lang = langSelect.value;
    render();
  });

  const onlyMissing = el("input", { type: "checkbox", checked: state.onlyMissing }, []);
  onlyMissing.addEventListener("change", () => {
    state.onlyMissing = onlyMissing.checked;
    render();
  });

  const codeInput = el("input", { type: "text", size: 8, placeholder: BoardI18n.t("tr.code") }, []);
  const nameInput = el("input", { type: "text", size: 16, placeholder: BoardI18n.t("tr.name") }, []);
  const addButton = el("button", { type: "button", textContent: BoardI18n.t("tr.add") }, []);
  addButton.addEventListener("click", () => addLanguage(codeInput.value, nameInput.value));

  const headerRow = el("tr", {}, [
    el("th", { textContent: BoardI18n.t("tr.key") }, []),
    el("th", { textContent: BoardI18n.t("tr.en") }, []),
    el("th", { textContent: BoardI18n.t("tr.tr") }, []),
  ]);
  const rows = shown.map((key) => {
    const text = translationOf(state.lang, key);
    const english = state.strings.en[key];
    const input = el("input", { type: "text", value: text, placeholder: text ? "" : english }, []);
    input.addEventListener("change", () => saveText(input, key));
    return el("tr", { className: text ? "" : "tr-miss" }, [
      el("td", { className: "mono", textContent: key }, []),
      el("td", { textContent: english }, []),
      el("td", {}, [input]),
    ]);
  });

  const root = state.root;
  root.textContent = "";
  root.appendChild(el("h2", { textContent: BoardI18n.t("tr.title") }, []));
  root.appendChild(el("p", { textContent: BoardI18n.t("tr.hint") }, []));
  root.appendChild(el("div", { className: "tr-bar" }, [
    el("label", { textContent: BoardI18n.t("tr.lang") + " " }, [langSelect]),
    el("label", { textContent: BoardI18n.t("tr.missing") + " " }, [onlyMissing]),
    codeInput,
    nameInput,
    addButton,
  ]));
  root.appendChild(el("p", { className: "bad", textContent: state.error }, []));
  root.appendChild(el("p", {
    className: "mono",
    textContent: BoardI18n.t("tr.saved", { n: missing.length, total: keys.length }),
  }, []));
  root.appendChild(el("table", { className: "t" }, [headerRow, ...rows]));
}

async function open() {
  if (!state.root) {
    state.root = el("section", { className: "bvi18n" }, []);
    (document.getElementById("board") || document.body).appendChild(state.root);
  }
  try {
    const view = await readI18n();
    state.languages = view.languages;
    state.strings = view.strings;
    const targets = languagesToTranslate();
    if (!targets.some((lang) => lang.code === state.lang)) {
      state.lang = targets.length ? targets[0].code : "en";
    }
    state.error = "";
  } catch (err) {
    state.error = err.message;
  }
  render();
}

const BoardI18nUI = { open };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardI18nUI;
} else {
  globalThis.BoardI18nUI = BoardI18nUI;
  document.addEventListener("boardviews:translations", () => open());
}
})();
