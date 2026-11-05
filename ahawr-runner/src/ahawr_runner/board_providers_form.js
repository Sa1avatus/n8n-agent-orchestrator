/* board-scope-wrapped */
(function () {
/* Board providers form (T023): the add or edit form of one provider, its key
 * field and its models table, rendered by board_providers_view.js.
 *
 * build(provider, ui) returns the form element; provider is null for a new one.
 * ui comes from the view: L, el, keyText, write(method, url, body) (sends with the
 * dashboard header), notify(text) (showNotice of board_dnd.js), reload(), edit(name)
 * and close().
 *
 * A save shows what changes and asks for confirmation before it writes: a provider
 * save sends dry_run first and takes the diff from the server, a model save compares
 * the fields with its row. Each write carries expected_version; a 409 conflict goes
 * to notify and the page reloads.
 * The key is write-only: its input is read once for the request and cleared right
 * after the request is sent; no response value is put into a field.
 */
"use strict";

function keyBody(value) {
  return value.trim() ? { key: value } : null;
}

function numberOrText(text) {
  if (text.trim() === "") return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : text;
}

function modelFieldsFrom(raw) {
  let capabilities = null;
  if (raw.capabilities.trim() !== "") {
    try {
      capabilities = JSON.parse(raw.capabilities);
    } catch (err) {
      return { error: err.message };
    }
  }
  return {
    fields: {
      alias: raw.alias,
      price_input_per_mtok_usd: numberOrText(raw.price_input),
      price_output_per_mtok_usd: numberOrText(raw.price_output),
      supports_thinking: raw.supports_thinking,
      api_type: raw.api_type || null,
      capability_overrides: capabilities,
    },
  };
}

// shortcut: an unknown api_type in the stored data throws here and blanks the page, the server validates it on save, upgrade: catch per model
function modelNotes(provider, model) {
  const apiType = model.api_type || provider.api_type;
  const capabilities = BoardProviders.effectiveCapabilities(apiType, model.capability_overrides);
  return {
    noWorker: BoardProviders.workerWarning(capabilities) !== null ||
      Boolean(model.probe_result && model.probe_result.worker_warning),
    notSynced: BoardProviders.routeNote(apiType) === "litellm",
  };
}

function modelChanges(row, fields) {
  const changed = {};
  for (const [key, after] of Object.entries(fields)) {
    const before = row[key] ?? null;
    if (JSON.stringify(before) !== JSON.stringify(after ?? null)) changed[key] = { before, after };
  }
  return changed;
}

function templateFields(template) {
  return { name: template.name, label: template.name, base_url: template.base_url, api_type: template.api_type };
}

function diffText(diff) {
  return Object.entries(diff)
    .map(([key, change]) => key + ": " + JSON.stringify(change.before) + " -> " + JSON.stringify(change.after))
    .join("\n");
}

function providerUrl(name) {
  return "/api/board/providers/" + encodeURIComponent(name);
}

function labelled(ui, key, en, control, extra) {
  return ui.el("label", { className: extra || "" }, [ui.el("span", { textContent: ui.L(key, en) }, []), control]);
}

function header(ui, key, en) {
  return ui.el("th", { textContent: ui.L(key, en) }, []);
}

function yesNo(flag, ui) {
  return flag ? ui.L("prov.yes", "yes") : ui.L("prov.no", "no");
}

function failed(err, ui) {
  if (err.status !== 409) return ui.notify(err.message);
  ui.notify(ui.L("prov.conflict", "Version conflict: the row was changed since it was read. The page is reloaded; check the values and save again."));
  return ui.reload();
}

function apiSelect(current, ui, withInherit) {
  const options = BoardProviders.API_TYPES.map((row) =>
    ui.el("option", { value: row.id, textContent: row.label, selected: row.id === current }, []));
  if (withInherit) {
    options.unshift(ui.el("option", {
      value: "",
      textContent: ui.L("prov.models.inherit", "provider default"),
      selected: !current,
    }, []));
  }
  return ui.el("select", {}, options);
}

function fillTypeNotes(node, apiType, ui) {
  node.textContent = "";
  if (BoardProviders.routeNote(apiType) === "litellm") {
    node.appendChild(ui.el("p", { textContent: ui.L("prov.form.litellm", "This type goes through LiteLLM. Its models are not synced from the database: add them to litellm/config.yaml by hand (LiteLLM steps).") }, []));
  }
  const extra = BoardProviders.fieldsForType(apiType);
  if (extra.length) {
    node.appendChild(ui.el("p", { className: "mono", textContent: ui.L("prov.form.extra", "Sent with each request: {fields}", { fields: extra.join(", ") }) }, []));
  }
}

async function saveProvider(provider, inputs, ui) {
  const isNew = provider === null;
  const name = isNew ? inputs.name.value : provider.name;
  const fields = {
    label: inputs.label.value,
    api_type: inputs.api_type.value,
    base_url: inputs.base_url.value,
    enabled: inputs.enabled.checked,
    notes: inputs.notes.value,
  };
  const error = BoardProviders.validateProviderForm(fields, { isNew, name });
  if (error) return ui.notify(error);
  const saved = ui.L("prov.form.saved", "Provider {name} saved.", { name });
  try {
    if (isNew) {
      await ui.write("POST", "/api/board/providers", BoardProviders.buildSavePayload(fields, { isNew, name }));
      ui.notify(saved);
      return ui.edit(name);
    }
    const body = BoardProviders.buildSavePayload(fields, { isNew, name, expectedVersion: provider.version });
    const preview = await ui.write("PUT", providerUrl(name), { ...body, dry_run: true });
    if (!preview.changed) return ui.notify(ui.L("prov.form.nothing", "No changes to save."));
    const question = ui.L("prov.form.diff", "Save these changes to {name}?\n{diff}", { name, diff: diffText(preview.diff) });
    if (!globalThis.confirm(question)) return undefined;
    await ui.write("PUT", providerUrl(name), body);
    ui.notify(saved);
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

async function saveKey(name, input, ui) {
  const body = keyBody(input.value);
  if (!body) return ui.notify(ui.L("prov.key.required", "Enter a key first."));
  const sent = ui.write("PUT", providerUrl(name) + "/key", body);
  input.value = "";
  try {
    await sent;
    ui.notify(ui.L("prov.key.saved", "Key of {name} saved. The value is not shown again.", { name }));
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

function keySection(provider, ui) {
  const input = ui.el("input", { type: "password", autocomplete: "off" }, []);
  const save = ui.el("button", { type: "button", textContent: ui.L("prov.key.save", "Save key") }, []);
  save.addEventListener("click", () => saveKey(provider.name, input, ui));
  return ui.el("div", { className: "bvprov-key bvprov-card" }, [
    ui.el("h4", { textContent: ui.L("prov.key.title", "API key") }, []),
    ui.el("p", { textContent: ui.keyText(provider.key_state) }, []),
    labelled(ui, "prov.key.field", "New key", input),
    save,
  ]);
}

function modelInputs(model, ui) {
  return {
    alias: ui.el("input", { type: "text", value: model.alias ?? "" }, []),
    price_input: ui.el("input", { type: "text", value: String(model.price_input_per_mtok_usd ?? "") }, []),
    price_output: ui.el("input", { type: "text", value: String(model.price_output_per_mtok_usd ?? "") }, []),
    supports_thinking: ui.el("input", { type: "checkbox", checked: Boolean(model.supports_thinking) }, []),
    api_type: apiSelect(model.api_type ?? "", ui, true),
    capabilities: ui.el("textarea", {
      value: model.capability_overrides ? JSON.stringify(model.capability_overrides) : "",
    }, []),
  };
}

function cellNodes(cells, ui) {
  return [
    ui.el("td", {}, [cells.alias]),
    ui.el("td", {}, [cells.price_input]),
    ui.el("td", {}, [cells.price_output]),
    ui.el("td", {}, [cells.supports_thinking]),
    ui.el("td", {}, [cells.api_type]),
    ui.el("td", {}, [cells.capabilities]),
  ];
}

function readModel(cells) {
  return {
    alias: cells.alias.value,
    price_input: cells.price_input.value,
    price_output: cells.price_output.value,
    supports_thinking: cells.supports_thinking.checked,
    api_type: cells.api_type.value,
    capabilities: cells.capabilities.value,
  };
}

async function saveModel(provider, model, cells, ui) {
  const parsed = modelFieldsFrom(readModel(cells));
  if (parsed.error) return ui.notify(ui.L("prov.models.caps_bad", "Capability overrides must be a JSON object: {error}", { error: parsed.error }));
  const changed = modelChanges(model, parsed.fields);
  if (!Object.keys(changed).length) return ui.notify(ui.L("prov.form.nothing", "No changes to save."));
  const question = ui.L("prov.models.diff", "Save these changes to model {model}?\n{diff}", { model: model.model, diff: diffText(changed) });
  if (!globalThis.confirm(question)) return undefined;
  const url = providerUrl(provider.name) + "/models/" + encodeURIComponent(model.model);
  try {
    await ui.write("PUT", url, { expected_version: model.version, ...parsed.fields });
    ui.notify(ui.L("prov.models.saved", "Model {model} saved.", { model: model.model }));
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

async function addModel(provider, nameInput, cells, ui) {
  const parsed = modelFieldsFrom(readModel(cells));
  if (parsed.error) return ui.notify(ui.L("prov.models.caps_bad", "Capability overrides must be a JSON object: {error}", { error: parsed.error }));
  const fields = Object.fromEntries(Object.entries(parsed.fields).filter(([, value]) => value !== null));
  const model = nameInput.value;
  try {
    await ui.write("POST", providerUrl(provider.name) + "/models", { model, ...fields });
    ui.notify(ui.L("prov.models.added", "Model {model} added.", { model }));
    return ui.reload();
  } catch (err) {
    return failed(err, ui);
  }
}

function probeText(model, ui) {
  const probe = model.probe_result;
  if (!probe) return "";
  return ui.L("prov.probe_row", "tools {tools}, streaming {streaming}, system {system}", {
    tools: yesNo(probe.tools, ui),
    streaming: yesNo(probe.streaming, ui),
    system: yesNo(probe.system, ui),
  });
}

function warningNodes(notes, ui) {
  const nodes = [];
  if (notes.noWorker) {
    nodes.push(ui.el("p", { className: "bad", textContent: ui.L("prov.models.worker_no", "not usable as a Worker") }, []));
  }
  if (notes.notSynced) {
    nodes.push(ui.el("p", { textContent: ui.L("prov.models.not_synced", "not synced, apply the LiteLLM steps") }, []));
  }
  return nodes;
}

function modelRow(provider, model, ui) {
  const cells = modelInputs(model, ui);
  const save = ui.el("button", { type: "button", textContent: ui.L("prov.models.save", "Save model") }, []);
  save.addEventListener("click", () => saveModel(provider, model, cells, ui));
  return ui.el("tr", {}, [
    ui.el("td", { className: "mono", textContent: model.model }, []),
    ...cellNodes(cells, ui),
    ui.el("td", { textContent: probeText(model, ui) }, []),
    ui.el("td", {}, warningNodes(modelNotes(provider, model), ui)),
    ui.el("td", {}, [save]),
  ]);
}

function newModelRow(provider, ui) {
  const nameInput = ui.el("input", { type: "text" }, []);
  const cells = modelInputs({}, ui);
  const add = ui.el("button", { type: "button", textContent: ui.L("prov.models.add", "Add model") }, []);
  add.addEventListener("click", () => addModel(provider, nameInput, cells, ui));
  return ui.el("tr", {}, [
    ui.el("td", {}, [nameInput]),
    ...cellNodes(cells, ui),
    ui.el("td", {}, []),
    ui.el("td", {}, []),
    ui.el("td", {}, [add]),
  ]);
}

const MAX_LISTED_MODELS = 300;

function listedPrice(model, ui) {
  if (model.free === true) return ui.L("prov.free", "free");
  if (model.price_in == null && model.price_out == null) return "";
  return String(model.price_in ?? "-") + " / " + String(model.price_out ?? "-");
}

async function addPicked(provider, chosen, ui) {
  let added = 0;
  let skipped = 0;
  for (const model of chosen) {
    const fields = { model: model.id };
    if (model.price_in != null) fields.price_input_per_mtok_usd = model.price_in;
    if (model.price_out != null) fields.price_output_per_mtok_usd = model.price_out;
    try {
      await ui.write("POST", providerUrl(provider.name) + "/models", fields);
      added += 1;
    } catch (err) {
      if (err.status === 409) throw err;
      skipped += 1;
    }
  }
  ui.notify(ui.L("prov.fetch.added", "{added} models added, {skipped} skipped.", { added, skipped }));
}

function listedRow(model, picked, redraw, ui) {
  const box = ui.el("input", { type: "checkbox", checked: picked.has(model.id) }, []);
  box.addEventListener("change", () => {
    if (box.checked) picked.add(model.id);
    else picked.delete(model.id);
    redraw();
  });
  return ui.el("label", { className: "bvprov-listed" }, [
    box,
    ui.el("span", { className: "mono", textContent: model.id }, []),
    ui.el("small", { textContent: listedPrice(model, ui) }, []),
  ]);
}

function showListed(panel, provider, body, ui) {
  panel.textContent = "";
  if (body.status !== 200) {
    const status = body.status == null ? "-" : String(body.status);
    panel.appendChild(ui.el("p", { className: "bad", textContent: ui.L("prov.fetch.failed", "The provider did not return a model list (status {status}).", { status }) }, []));
    return;
  }
  const have = new Set((provider.models || []).map((m) => m.model));
  const models = body.models.filter((m) => !have.has(m.id));
  const picked = new Set();
  const filter = ui.el("input", { type: "text", placeholder: ui.L("prov.fetch.filter", "Filter by name") }, []);
  const freeOnly = ui.el("input", { type: "checkbox" }, []);
  const count = ui.el("small", {}, []);
  const list = ui.el("div", { className: "bvprov-listing" }, []);
  const add = ui.el("button", { type: "button", className: "pri" }, []);
  const all = ui.el("button", { type: "button", textContent: ui.L("prov.fetch.all", "Select shown") }, []);
  const shown = () => models.filter((m) => (!freeOnly.checked || m.free === true)
    && m.id.toLowerCase().includes(filter.value.trim().toLowerCase()));
  const redraw = () => {
    const rows = shown();
    list.textContent = "";
    for (const model of rows.slice(0, MAX_LISTED_MODELS)) list.appendChild(listedRow(model, picked, redraw, ui));
    count.textContent = ui.L("prov.fetch.count", "{shown} of {total} models", { shown: rows.length, total: models.length });
    add.textContent = ui.L("prov.fetch.add", "Add selected ({n})", { n: picked.size });
  };
  filter.addEventListener("input", redraw);
  freeOnly.addEventListener("change", redraw);
  all.addEventListener("click", () => {
    for (const model of shown().slice(0, MAX_LISTED_MODELS)) picked.add(model.id);
    redraw();
  });
  add.addEventListener("click", async () => {
    if (!picked.size) return;
    try {
      await addPicked(provider, models.filter((m) => picked.has(m.id)), ui);
      await ui.reload();
    } catch (err) {
      await failed(err, ui);
    }
  });
  const free = ui.el("label", {}, [freeOnly, ui.el("span", { textContent: ui.L("prov.fetch.free", "Free only") }, [])]);
  panel.appendChild(ui.el("div", { className: "bvprov-fetchbar" }, [filter, free, all, add]));
  panel.appendChild(count);
  panel.appendChild(list);
  redraw();
}

function fetchControls(provider, ui) {
  const panel = ui.el("div", { className: "bvprov-fetch" }, []);
  const button = ui.el("button", { type: "button", textContent: ui.L("prov.fetch", "Fetch models") }, []);
  button.addEventListener("click", async () => {
    try {
      showListed(panel, provider, await ui.read(providerUrl(provider.name) + "/available-models"), ui);
    } catch (err) {
      await failed(err, ui);
    }
  });
  return { button, panel };
}

function modelsSection(provider, ui) {
  const models = provider.models || [];
  const head = ui.el("tr", {}, [
    header(ui, "prov.form.name", "Name"),
    header(ui, "prov.models.col.alias", "Alias"),
    header(ui, "prov.models.col.in", "Price in, USD per 1M tokens"),
    header(ui, "prov.models.col.out", "Price out, USD per 1M tokens"),
    header(ui, "prov.models.col.thinking", "Thinking"),
    header(ui, "prov.models.col.type", "API type override"),
    header(ui, "prov.models.col.caps", "Capability overrides"),
    header(ui, "prov.probe", "Probe"),
    header(ui, "prov.col.note", "Note"),
    header(ui, "prov.col.apply", "Apply"),
  ]);
  const rows = models.map((model) => modelRow(provider, model, ui));
  const fetcher = fetchControls(provider, ui);
  return ui.el("div", { className: "bvprov-card" }, [
    ui.el("div", { className: "bvprov-sechead" }, [
      ui.el("h4", { textContent: ui.L("prov.models.title", "Models") }, []),
      fetcher.button,
    ]),
    fetcher.panel,
    models.length ? null : ui.el("p", { textContent: ui.L("prov.models.none", "No models yet.") }, []),
    ui.el("div", { className: "bvprov-tablewrap" }, [
      ui.el("table", { className: "t" }, [head, ...rows, newModelRow(provider, ui)]),
    ]),
    ui.el("p", { textContent: ui.L("prov.models.caps_note", "JSON object with only the values that differ from the API type, e.g. tools set to false.") }, []),
  ].filter(Boolean));
}

function templateRow(ui, inputs, typeNotes) {
  const templates = ui.templates || [];
  const select = ui.el("select", {}, [
    ui.el("option", { value: "", textContent: ui.L("prov.form.template", "Add from template") }, []),
    ...templates.map((template) => ui.el("option", { value: template.name, textContent: template.name }, [])),
  ]);
  const hint = ui.el("p", { className: "bvprov-template-hint" }, []);
  const applyTemplate = () => {
    const template = templates.find((row) => row.name === select.value);
    if (!template) return (hint.textContent = "");
    const fields = templateFields(template);
    inputs.name.value = fields.name;
    inputs.label.value = fields.label;
    inputs.base_url.value = fields.base_url;
    inputs.api_type.value = fields.api_type;
    fillTypeNotes(typeNotes, fields.api_type, ui);
    hint.textContent = template.hint + (template.verified
      ? ""
      : " " + ui.L("prov.form.template_unverified", "unverified"));
  };
  const apply = ui.el("button", { type: "button", textContent: ui.L("prov.form.template_apply", "Apply") }, []);
  apply.addEventListener("click", applyTemplate);
  select.addEventListener("change", applyTemplate);
  if (ui.template) {
    select.value = ui.template;
    applyTemplate();
  }
  return ui.el("div", {}, [
    ui.el("label", {}, [ui.el("span", { textContent: ui.L("prov.form.template", "Add from template") }, []), select]),
    apply,
    hint,
  ]);
}

async function deleteProvider(provider, ui) {
  const question = ui.L("prov.delete_confirm", "Delete provider {name}? Its models and its stored key are removed. This cannot be undone.", { name: provider.name });
  if (!globalThis.confirm(question)) return undefined;
  const url = providerUrl(provider.name) + "?expected_version=" + encodeURIComponent(provider.version);
  try {
    await ui.write("DELETE", url);
  } catch (err) {
    return ui.notify(err.message);
  }
  ui.notify(ui.L("prov.deleted", "Provider {name} deleted.", { name: provider.name }));
  ui.close();
  return ui.reload();
}

function providerFields(provider, isNew, ui) {
  const values = provider || {
    label: "", api_type: BoardProviders.API_TYPES[0].id, base_url: "", enabled: true, notes: "",
  };
  const inputs = {
    name: ui.el("input", { type: "text", value: isNew ? "" : provider.name, disabled: !isNew }, []),
    label: ui.el("input", { type: "text", value: values.label }, []),
    api_type: apiSelect(values.api_type, ui, false),
    base_url: ui.el("input", { type: "text", value: values.base_url }, []),
    enabled: ui.el("input", { type: "checkbox", checked: values.enabled }, []),
    notes: ui.el("textarea", { value: values.notes }, []),
  };
  const typeNotes = ui.el("div", {}, []);
  const showTypeNotes = () => fillTypeNotes(typeNotes, inputs.api_type.value, ui);
  inputs.api_type.addEventListener("change", showTypeNotes);
  showTypeNotes();
  const save = ui.el("button", { type: "button", className: "pri", textContent: ui.L("prov.form.save", "Save") }, []);
  save.addEventListener("click", () => saveProvider(provider, inputs, ui));
  const close = ui.el("button", { type: "button", textContent: ui.L("prov.close", "Close") }, []);
  close.addEventListener("click", () => ui.close());
  const parts = [
    labelled(ui, "prov.form.name", "Name", inputs.name),
    labelled(ui, "prov.form.label", "Label", inputs.label),
    labelled(ui, "prov.form.api", "API type", inputs.api_type),
    typeNotes,
    labelled(ui, "prov.form.url", "Base URL", inputs.base_url),
    labelled(ui, "prov.form.enabled", "Enabled", inputs.enabled, "check"),
    labelled(ui, "prov.form.notes", "Notes", inputs.notes, "wide"),
    ui.el("div", { className: "bvprov-actions wide" }, [save, close]),
  ];
  if (isNew) parts.unshift(templateRow(ui, inputs, typeNotes));
  return ui.el("div", { className: "bvprov-card" }, [
    ui.el("h4", { textContent: ui.L("prov.connection", "Connection") }, []),
    ui.el("div", { className: "bvprov-fields" }, parts),
  ]);
}

function topRow(provider, title, ui) {
  const children = [ui.el("h3", { textContent: title }, [])];
  if (provider) {
    const off = !provider.enabled;
    children.push(ui.el("span", { className: "bvprov-pill" + (off ? " off" : ""), textContent: off ? ui.L("prov.off", "off") : ui.L("prov.on", "enabled") }, []));
    const remove = ui.el("button", { type: "button", className: "danger", textContent: ui.L("prov.delete", "Delete provider") }, []);
    remove.addEventListener("click", () => deleteProvider(provider, ui));
    children.push(ui.el("span", { className: "bvprov-danger-slot" }, [remove]));
  }
  return ui.el("div", { className: "bvprov-top" }, children);
}

function build(provider, ui) {
  const isNew = provider === null;
  const title = isNew
    ? ui.L("prov.form.new", "New provider")
    : ui.L("prov.form.edit", "Provider {name}", { name: provider.name });
  const parts = [topRow(provider, title, ui), providerFields(provider, isNew, ui)];
  if (!isNew) parts.push(keySection(provider, ui), modelsSection(provider, ui));
  return ui.el("section", { className: "bvprov-form" }, parts);
}

const BoardProvidersForm = {
  build,
  keyBody,
  numberOrText,
  templateFields,
  modelFieldsFrom,
  modelNotes,
  modelChanges,
  diffText,
};
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardProvidersForm;
} else {
  globalThis.BoardProvidersForm = BoardProvidersForm;
}
})();
