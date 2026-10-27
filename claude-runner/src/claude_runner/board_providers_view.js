/* board-scope-wrapped */
(function () {
/* Board providers page (T022): the screen opened from the header Providers
 * button (#bvprov in board.html).
 *
 * open() reads GET /api/board/providers and GET /api/board/providers/import-from-env
 * and renders into #board: the provider list (name, API type, base URL, model
 * count, key state, enabled), the import proposal from the env blocks (read-only
 * rows, one Create per row) and, per provider, a model select with Test and
 * Probe. Test and Probe send real requests, so each asks for confirmation first.
 * Writes go through providerWrite, which carries the dashboard header.
 * The add and edit form is board_providers_form.js; its notices go through showNotice.
 *
 * A key value is never read: only key_state.has_key and key_state.set_at are shown.
 * Every value is put into the DOM with textContent or the value property.
 */
"use strict";

const state = { providers: [], proposals: [], templates: [], notice: "", error: "", root: null, form: null };

function L(key, en, params) {
  const text = t(key, params);
  if (text !== key) return text;
  return en.replace(/\{(\w+)\}/g, (m, name) => (params && params[name] !== undefined ? String(params[name]) : m));
}

function el(tag, props, children) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  for (const child of children) node.appendChild(child);
  return node;
}

function th(key, en) {
  return el("th", { textContent: L(key, en) }, []);
}

async function unwrap(res) {
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error((payload.error && payload.error.message) || "HTTP " + res.status);
    err.status = res.status;
    throw err;
  }
  return payload;
}

async function providerWrite(method, url, body) {
  return unwrap(await fetch(url, {
    method,
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-AHAWR-Dashboard": "1" },
    body: JSON.stringify(body),
  }));
}

async function readProviders() {
  const [list, proposals, templates] = await Promise.all([
    fetch("/api/board/providers", { cache: "no-store" }).then(unwrap),
    fetch("/api/board/providers/import-from-env", { cache: "no-store" }).then(unwrap),
    fetch("/api/board/providers/templates", { cache: "no-store" }).then(unwrap),
  ]);
  state.providers = list.providers;
  state.proposals = proposals.proposals;
  state.templates = templates.templates;
}

function reload() {
  return readProviders().then(
    () => { state.error = ""; },
    (err) => { state.error = err.message; },
  ).then(render);
}

function act(request, noticeOf) {
  return request.then(
    (result) => {
      state.error = "";
      state.notice = noticeOf(result);
      return reload();
    },
    (err) => {
      state.notice = "";
      state.error = err.message;
      render();
    },
  );
}

function yesNo(flag) {
  return flag ? L("prov.yes", "yes") : L("prov.no", "no");
}

function keyText(keyState) {
  return keyState.has_key
    ? L("prov.key_yes", "key set, since {when}", { when: keyState.set_at || "-" })
    : L("prov.key_no", "no key");
}

function testNotice(name, model, result) {
  const status = result.status == null ? L("prov.no_answer", "no answer") : String(result.status);
  return L("prov.test_done", "Test of {name}/{model}: status {status}, {ms} ms.",
    { name, model, status, ms: result.latency_ms });
}

function probeNotice(name, model, probe) {
  return L("prov.probe_done",
    "Probe of {name}/{model}: tools {tools}, streaming {streaming}, system message {system}.",
    { name, model, tools: yesNo(probe.tools), streaming: yesNo(probe.streaming), system: yesNo(probe.system) });
}

function runCheck(name, model, kind) {
  const cost = L("prov.cost", "Test and Probe send real requests to the provider and may cost money. Continue?");
  if (!globalThis.confirm(cost)) return Promise.resolve();
  const url = "/api/board/providers/" + encodeURIComponent(name) + "/" + kind;
  return act(providerWrite("POST", url, { model }), (result) => (kind === "test"
    ? testNotice(name, model, result)
    : probeNotice(name, model, result.probe)));
}

function createFromProposal(proposal) {
  const body = { name: proposal.name, ...proposal.fields };
  return act(providerWrite("POST", "/api/board/providers", body), () =>
    L("prov.created", "Provider {name} created.", { name: proposal.name }));
}

function providerRow(provider) {
  const models = provider.models || [];
  const select = el("select", {}, models.map((m) =>
    el("option", { value: m.model, textContent: m.model }, [])));
  const test = el("button", { type: "button", textContent: L("prov.test", "Test") }, []);
  const probe = el("button", { type: "button", textContent: L("prov.probe", "Probe") }, []);
  test.addEventListener("click", () => runCheck(provider.name, select.value, "test"));
  probe.addEventListener("click", () => runCheck(provider.name, select.value, "probe"));
  const checks = models.length
    ? [select, test, probe]
    : [el("span", { textContent: L("prov.no_models", "no models") }, [])];
  const edit = el("button", { type: "button", textContent: L("prov.edit", "Edit") }, []);
  edit.addEventListener("click", () => openForm(provider.name));
  // the row's own name opens the same form, so a click targets one row unambiguously
  const nameCell = el("td", { className: "mono", textContent: provider.name }, []);
  nameCell.addEventListener("click", () => openForm(provider.name));
  return el("tr", {}, [
    nameCell,
    el("td", { textContent: provider.api_type }, []),
    el("td", { className: "mono", textContent: provider.base_url }, []),
    el("td", { textContent: String(models.length) }, []),
    el("td", { textContent: keyText(provider.key_state) }, []),
    el("td", { textContent: yesNo(provider.enabled) }, []),
    el("td", {}, checks),
    el("td", {}, [edit]),
  ]);
}

function openForm(name) {
  state.form = { name };
  state.notice = "";
  render();
}

function formDeps() {
  return {
    L,
    el,
    keyText,
    write: providerWrite,
    templates: state.templates,
    notify: (text) => {
      state.notice = text;
      globalThis.BoardDnd.showNotice(text);
    },
    reload,
    edit: (name) => {
      state.form = { name };
      return reload();
    },
    close: () => {
      state.form = null;
      render();
    },
  };
}

function formNode() {
  const name = state.form.name;
  const provider = name ? state.providers.find((p) => p.name === name) : null;
  if (name && !provider) {
    state.form = null;
    return null;
  }
  return BoardProvidersForm.build(provider, formDeps());
}

function proposalRow(proposal) {
  const create = el("button", {
    type: "button",
    textContent: L("prov.create", "Create"),
    disabled: proposal.exists || Boolean(proposal.error),
  }, []);
  create.addEventListener("click", () => createFromProposal(proposal));
  const note = proposal.error || (proposal.exists ? L("prov.exists", "already in the database") : "");
  return el("tr", {}, [
    el("td", { className: "mono", textContent: proposal.name }, []),
    el("td", { className: "mono", textContent: proposal.fields ? proposal.fields.base_url : "" }, []),
    el("td", { textContent: proposal.has_key ? L("prov.key_env", "key in the environment") : L("prov.key_no", "no key") }, []),
    el("td", { textContent: note }, []),
    el("td", {}, [create]),
  ]);
}

function providerTable() {
  if (!state.providers.length) return el("p", { textContent: L("prov.empty", "No providers yet.") }, []);
  const head = el("tr", {}, [
    th("prov.col.name", "Name"),
    th("prov.col.api", "API type"),
    th("prov.col.url", "Base URL"),
    th("prov.col.models", "Models"),
    th("prov.col.key", "Key"),
    th("prov.col.enabled", "Enabled"),
    th("prov.col.check", "Check"),
    th("prov.edit", "Edit"),
  ]);
  return el("table", { className: "t" }, [head, ...state.providers.map(providerRow)]);
}

function importTable() {
  if (!state.proposals.length) return el("p", { textContent: L("prov.import_none", "Nothing to import.") }, []);
  const head = el("tr", {}, [
    th("prov.col.name", "Name"),
    th("prov.col.url", "Base URL"),
    th("prov.col.key", "Key"),
    th("prov.col.note", "Note"),
    th("prov.col.apply", "Apply"),
  ]);
  return el("table", { className: "t" }, [head, ...state.proposals.map(proposalRow)]);
}

function render() {
  const root = state.root;
  root.textContent = "";
  root.appendChild(el("h2", { textContent: L("prov.title", "Providers") }, []));
  root.appendChild(el("p", { textContent: L("prov.hint", "Stored in the database (board.providers). Keys are never shown: only whether a key is set and when it was set.") }, []));
  root.appendChild(el("p", { className: "bad", textContent: state.error }, []));
  root.appendChild(el("p", { className: "mono", textContent: state.notice }, []));
  root.appendChild(providerTable());
  const add = el("button", { type: "button", textContent: L("prov.add", "Add provider") }, []);
  add.addEventListener("click", () => openForm(""));
  root.appendChild(add);
  const form = state.form && formNode();
  if (form) root.appendChild(form);
  root.appendChild(el("h3", { textContent: L("prov.import_title", "Import from the environment") }, []));
  root.appendChild(el("p", { textContent: L("prov.import_hint", "Proposed from the provider blocks in the runner environment. Read-only: Create adds the provider to the database.") }, []));
  root.appendChild(importTable());
}

async function open() {
  if (!state.root) {
    state.root = el("section", { className: "bvprov" }, []);
    (document.getElementById("board") || document.body).appendChild(state.root);
  }
  state.notice = "";
  await reload();
}

const BoardProvidersView = { open };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardProvidersView;
} else {
  globalThis.BoardProvidersView = BoardProvidersView;
  document.getElementById("bvprov").addEventListener("click", () => open());
}
})();
