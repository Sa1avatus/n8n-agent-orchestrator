/* board-scope-wrapped */
(function () {
/* Board providers page (T022): the screen opened from the header Providers
 * button (#bvprov in board.html).
 *
 * open() reads GET /api/board/providers, /import-from-env and /templates and renders
 * a master-detail page into #board (the rest of the board is hidden meanwhile): on the
 * left the providers (name, key state), New provider and the import proposals from the
 * env blocks (one Create per row); on the right the form of the selected provider, a
 * model select with Test and Probe, or, with nothing selected, the templates to start
 * from. Test and Probe send real requests, so each asks for confirmation first.
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

function providerItem(provider) {
  const on = state.form && state.form.name === provider.name;
  const key = provider.key_state.has_key ? L("prov.key_short", "key set") : L("prov.key_no", "no key");
  const item = el("button", { type: "button", className: "bvprov-item" + (on ? " on" : "") }, [
    el("span", { className: "mono", textContent: provider.name }, []),
    el("small", { textContent: key + (provider.enabled ? "" : " · " + L("prov.off", "off")) }, []),
  ]);
  item.addEventListener("click", () => openForm(provider.name));
  return item;
}

function checksNode(provider) {
  const models = provider.models || [];
  const title = el("h3", { textContent: L("prov.col.check", "Check") }, []);
  if (!models.length) {
    return el("section", { className: "bvprov-checks" }, [title,
      el("p", { textContent: L("prov.no_models", "no models") }, [])]);
  }
  const select = el("select", {}, models.map((m) =>
    el("option", { value: m.model, textContent: m.model }, [])));
  const test = el("button", { type: "button", textContent: L("prov.test", "Test") }, []);
  const probe = el("button", { type: "button", textContent: L("prov.probe", "Probe") }, []);
  test.addEventListener("click", () => runCheck(provider.name, select.value, "test"));
  probe.addEventListener("click", () => runCheck(provider.name, select.value, "probe"));
  return el("section", { className: "bvprov-checks" }, [title, select, test, probe]);
}

function openForm(name, template) {
  state.form = { name, template };
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
    template: state.form ? state.form.template : undefined,
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
  const key = proposal.has_key ? L("prov.key_env", "key in the environment") : L("prov.key_no", "no key");
  const note = proposal.error || (proposal.exists ? L("prov.exists", "already in the database") : key);
  return el("div", { className: "bvprov-prop" }, [
    el("span", { className: "mono", textContent: proposal.name }, []),
    el("small", { textContent: proposal.fields ? proposal.fields.base_url : "" }, []),
    el("small", { textContent: note }, []),
    create,
  ]);
}

function importNode() {
  const rows = state.proposals.length
    ? state.proposals.map(proposalRow)
    : [el("p", { textContent: L("prov.import_none", "Nothing to import.") }, [])];
  return el("section", { className: "bvprov-import" }, [
    el("h3", { textContent: L("prov.import_title", "Import from the environment") }, []),
    el("p", { textContent: L("prov.import_hint", "Proposed from the provider blocks in the runner environment. Read-only: Create adds the provider to the database.") }, []),
    ...rows,
  ]);
}

function listNode() {
  const add = el("button", { type: "button", textContent: L("prov.form.new", "New provider") }, []);
  add.addEventListener("click", () => openForm(""));
  const items = state.providers.length
    ? state.providers.map(providerItem)
    : [el("p", { textContent: L("prov.empty", "No providers yet.") }, [])];
  return el("aside", { className: "bvprov-list" }, [...items, add, importNode()]);
}

function emptyDetail() {
  const chips = state.templates.map((template) => {
    const chip = el("button", { type: "button", textContent: template.name }, []);
    chip.addEventListener("click", () => openForm("", template.name));
    return chip;
  });
  const blank = el("button", { type: "button", textContent: L("prov.blank", "Blank provider") }, []);
  blank.addEventListener("click", () => openForm(""));
  return el("div", {}, [
    el("p", { textContent: L("prov.pick", "Select a provider on the left, or add one.") }, []),
    el("h3", { textContent: L("prov.templates_title", "From a template") }, []),
    el("p", { textContent: L("prov.templates_hint", "A template fills in the name, API type and base URL of a well-known provider; you only add the key.") }, []),
    el("div", { className: "bvprov-chips" }, [...chips, blank]),
  ]);
}

function detailNode() {
  const form = state.form && formNode();
  if (!form) return el("main", { className: "bvprov-detail" }, [emptyDetail()]);
  const provider = state.form.name && state.providers.find((p) => p.name === state.form.name);
  return el("main", { className: "bvprov-detail" }, provider ? [form, checksNode(provider)] : [form]);
}

function setPageOpen(open) {
  const board = document.getElementById("board");
  if (!board) return;
  const rest = String(board.className || "").split(/\s+/).filter((c) => c && c !== "provopen");
  board.className = (open ? [...rest, "provopen"] : rest).join(" ");
}

function closePage() {
  state.form = null;
  if (state.root) state.root.textContent = "";
  setPageOpen(false);
}

function render() {
  const root = state.root;
  root.textContent = "";
  const back = el("button", { type: "button", textContent: L("prov.page_close", "Back to missions") }, []);
  back.addEventListener("click", closePage);
  root.appendChild(el("div", { className: "bvprov-head" }, [
    el("h2", { textContent: L("prov.title", "Providers") }, []), back]));
  root.appendChild(el("p", { textContent: L("prov.hint", "Stored in the database (board.providers). Keys are never shown: only whether a key is set and when it was set.") }, []));
  root.appendChild(el("p", { className: "bad", textContent: state.error }, []));
  root.appendChild(el("p", { className: "mono", textContent: state.notice }, []));
  root.appendChild(el("div", { className: "bvprov-grid" }, [listNode(), detailNode()]));
}

async function open() {
  if (!state.root) {
    state.root = el("section", { className: "bvprov" }, []);
    (document.getElementById("board") || document.body).appendChild(state.root);
  }
  state.notice = "";
  setPageOpen(true);
  await reload();
  if (!state.form && state.providers.length) openForm(state.providers[0].name);
}

const BoardProvidersView = { open };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardProvidersView;
} else {
  globalThis.BoardProvidersView = BoardProvidersView;
  document.getElementById("bvprov").addEventListener("click", () => open());
  globalThis.addEventListener("hashchange", closePage);
}
})();
