# Providers: model, api types, templates

Status: implemented in 0.11.0 behind the flags `providers_db` and `litellm_sync` (both OFF by default). The
activation steps (sections 11-13) are not run. With the flags off a provider is a block of `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` variables (`config.py`, `_providers`; see
`README.md` "Providers per role"), and a model is the string `provider/model` in `roles_json` (`roles-config.md`).
The registry code ships behind a flag that is OFF by default; with the flag off, behaviour is byte-for-byte unchanged.

## 1. The model

One model is one entry in a provider's model list. Fields:

| Field | Type | Meaning |
|---|---|---|
| `provider` | string | The provider name; the `provider` part of `roles_json` `provider/model`. |
| `model` | string | The model name; the `model` part of `roles_json`. For a LiteLLM route this is the LiteLLM alias (section 5.2). |
| `price_input_per_mtok_usd` | number | Price per million input tokens, set by the operator from the vendor price page. Not verified here; no value is shipped. |
| `price_output_per_mtok_usd` | number | Price per million output tokens, same rule. |
| `supports_thinking` | bool | The model accepts a reasoning or thinking request field. |
| `api_type` | string, optional | Overrides the provider's `api_type` for this model only (section 5.3). |
| `capabilities` | object, optional | Overrides single capability values from section 3 for this model only. Only the keys that differ are listed. |

Example entry (design, not yet readable by the code):

```json
{"provider": "example", "model": "qwen-worker", "price_input_per_mtok_usd": 0, "price_output_per_mtok_usd": 0,
 "supports_thinking": false}
```

## 2. Routing rule

- `api_type` `anthropic`: the runner sends the run straight to the provider's Messages endpoint (or to the container's
  default Anthropic endpoint when the provider has no block).
- Every other `api_type`: the runner points Claude Code at LiteLLM, which speaks the Anthropic Messages API to Claude
  Code and translates to the upstream API. The upstream is chosen by the LiteLLM entry whose `litellm_params.model`
  starts with the LiteLLM prefix from section 3 (for example `openai/`). The existing LiteLLM route in
  `litellm/config.yaml` uses the same mechanism for the local llama.cpp server.

## 3. The api_types registry

### 3.1 Identity

Status: `verified` means the endpoint and auth were read from the vendor page named in section 8 during this design.
`unverified` means it was not, and the type is OFF by default.

| id | label | endpoint path | auth_style | needs_translator | LiteLLM prefix | extra fields | status | source |
|---|---|---|---|---|---|---|---|---|
| `anthropic` | Anthropic Messages | `POST /v1/messages` | `x-api-key` header | no | `anthropic/` (not used for direct routing) | `anthropic-version` header | verified (path, header, system field) | [S1] |
| `openai-chat` | OpenAI Chat Completions | `POST /chat/completions` (base `.../v1`) | `Authorization: Bearer` | yes (Anthropic to chat) | `openai/` (confirmed by the repo's `litellm/config.yaml` comment) | none | verified (path, operationId, `max_tokens`); auth header not quoted | [S2] |
| `openai-responses` | OpenAI Responses | `POST /responses` | `Authorization: Bearer` (not confirmed) | yes | `openai/` (LiteLLM Responses route not confirmed) | none | **unverified, OFF** | none retrieved |

### 3.2 Capabilities

Each cell is either the value from the named source or `unverified`. A capability with a value `unverified` is read as
false by the probe until a source confirms it.

| id | tools | parallel_tools | streaming | system_message_position | prompt_caching | reasoning_or_thinking | max_tokens_field | usage_in_stream | known_quirks |
|---|---|---|---|---|---|---|---|---|---|
| `anthropic` | unverified | unverified | unverified | top-level `system` (verified) | unverified | unverified | `max_tokens` (in the page example) | unverified | `anthropic-version` header; fetched summary called it optional |
| `openai-chat` | unverified | unverified | unverified | `messages` with role `system` (unverified) | unverified | unverified | `max_tokens` (verified in spec; `max_completion_tokens` not seen in the excerpt) | unverified | many local servers implement only part of the spec; LiteLLM drops unsupported fields (`drop_params: true`) |
| `openai-responses` | unverified | unverified | unverified | unverified | unverified | unverified | unverified | unverified | off by default |

The verified cells are the ones [S1] and [S2] show; the rest is the design's assumption and is checked by the probe
(section 7) before a model is used as a Worker.

### 3.3 Candidates checked and not shipped as enabled types

| Candidate | What was verified | Decision |
|---|---|---|
| Google Gemini native (`generateContent`) | Endpoint `POST https://generativelanguage.googleapis.com/v1beta/{model=models/*}:generateContent` [S4]. Examples pass the key as a `?key=` query parameter; `x-goog-api-key` is not documented on that page. Needs a translator. | **unverified, OFF**. A key in the URL is logged by proxies; do not ship without a header-based auth source. |
| AWS Bedrock Converse | `POST /model/{modelId}/converse` and the `bedrock:InvokeModel` permission [S5]. SigV4 signing is not quoted on that page. Needs a translator and a new signing dependency. | **unverified, OFF**. A new runtime dependency needs operator approval (CLAUDE.md "Ask first"). |
| Azure OpenAI | Chat path `POST https://{endpoint}/openai/deployments/{deployment-id}/chat/completions?api-version=...` and the `api-key` header [S6]. | Verified path and auth; **candidate, OFF** until a template and a LiteLLM prefix are confirmed (`azure/` is not confirmed). |

## 4. Vendor templates

`claude-runner/src/claude_runner/provider_templates.json` It holds public endpoints only:
no keys, no operator hosts, no resource names. Each row has `status`; a value without a source is `unverified` and the
row is OFF.

```json
{
  "anthropic": {"api_type": "anthropic", "base_url": "https://api.anthropic.com", "status": "verified", "source": "S1"},
  "gemini-openai": {"api_type": "openai-chat", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                    "status": "verified", "source": "S3"},
  "azure-openai": {"api_type": "openai-chat", "base_url": "https://{resource}.openai.azure.com",
                   "status": "unverified", "source": "S6",
                   "hint": "not native Azure: the deployment path and api-version are added per model and are not encoded here"},
  "openrouter": {"api_type": "openai-chat", "base_url": "https://openrouter.ai/api/v1",
                 "status": "unverified", "source": "none", "hint": "third-party aggregator; auth not checked"},
  "deepseek": {"api_type": "openai-chat", "base_url": "https://api.deepseek.com/v1",
               "status": "unverified", "source": "none", "hint": "vendor OpenAI-compatible endpoint; auth not checked"},
  "mistral": {"api_type": "openai-chat", "base_url": "https://api.mistral.ai/v1",
              "status": "unverified", "source": "none", "hint": "vendor OpenAI-compatible endpoint; auth not checked"},
  "together": {"api_type": "openai-chat", "base_url": "https://api.together.ai/v1",
               "status": "unverified", "source": "none", "hint": "vendor OpenAI-compatible endpoint; auth not checked"},
  "groq": {"api_type": "openai-chat", "base_url": "https://api.groq.com/openai/v1",
           "status": "unverified", "source": "none", "hint": "vendor OpenAI-compatible endpoint; auth not checked"},
  "ollama": {"api_type": "openai-chat", "base_url": "http://localhost:11434/v1",
             "status": "unverified", "source": "none", "hint": "local server; the operator's instance, no key"},
  "vllm": {"api_type": "openai-chat", "base_url": "http://localhost:8000/v1",
           "status": "unverified", "source": "none", "hint": "local vLLM server; the operator's instance"},
  "llamacpp": {"api_type": "openai-chat", "base_url": "http://localhost:8080/v1",
               "status": "unverified", "source": "none",
               "hint": "local llama-server default port; the repo instance uses the operator's LLAMACPP_API_BASE (litellm/config.yaml)"}
}
```

`{resource}` is a placeholder the operator fills in; the template never holds a real resource name. Every `api_type`
in a template names a row of table 3.1; `azure-openai` is not a type, only the template name, and the row is OFF
because its exact path was not confirmed.

## 5. Worked examples

### 5.1 An anthropic provider

`roles_json`:

```json
{"worker": "anthropic/haiku"}
```

The provider `anthropic` has no block, so the run uses the container credential and goes to Anthropic directly
(`README.md`, "Providers per role"). The model `haiku` is passed to Claude Code unchanged. No LiteLLM entry is needed.

### 5.2 An openai-chat provider through LiteLLM

Provider block (variable names only; values are set by the operator in the environment, never in this doc):

```
CLAUDE_RUNNER_PROVIDER_EXAMPLE__ANTHROPIC_BASE_URL=<litellm-url>
CLAUDE_RUNNER_PROVIDER_EXAMPLE__ANTHROPIC_AUTH_TOKEN=<litellm-master-key-name>
```

The provider's `api_type` is `openai-chat`, so the run goes through LiteLLM (section 2). Add these lines to
`litellm/config.yaml`, ABOVE the `model_name: "*"` entry, because the first matching entry wins:

```yaml
  - model_name: qwen-worker                # the alias: equals the model in roles_json
    litellm_params:
      model: openai/qwen3-example          # "openai/" prefix + the upstream model name
      api_base: os.environ/EXAMPLE_API_BASE
      api_key: os.environ/EXAMPLE_API_KEY
```

`roles_json` then names the provider and the alias:

```json
{"worker": "example/qwen-worker"}
```

Alias rule: the `model` part of `roles_json` (here `qwen-worker`) must equal the LiteLLM `model_name`. If it does not,
the request falls through to the `"*"` entry and goes to the default llama-server with the wrong model name.

### 5.3 One endpoint serving two types through a model override

Case: one host answers Anthropic Messages on `/v1/messages` and OpenAI Chat on `/chat/completions`. The provider's
default `api_type` is `anthropic`; one model overrides it:

```json
{"provider": "shared", "model": "claude-like-model", "api_type": "anthropic"},
{"provider": "shared", "model": "chat-only-model", "api_type": "openai-chat",
 "capabilities": {"parallel_tools": false}}
```

The first model routes direct (section 2); the second goes through LiteLLM with the `openai/` prefix. The override
changes only `api_type` and the listed capabilities; everything else comes from the provider.

## 6. Adding a type or a template

### 6.1 Add an api type (one row)

Add one row to the identity table (3.1) and one to the capability table (3.2), and one line to the prefix map the
LiteLLM route reads. The row needs a `status` and a `source`; without a verified source it is `unverified` and OFF.
No other code path changes: the router reads the registry by `id`.

### 6.2 Add a vendor template (one row)

Add one object to `provider_templates.json` (section 4): `api_type`, public `base_url`, `status`, `source`. Never put a
key, a resource name or a private host in it.

## 7. The capability probe and the "not usable as a Worker" warning

Design:

- Before a model is used, the probe calls the endpoint's model list (`GET /v1/models`, the same call the context probe
  uses, `README.md`) and a one-token request with a tool definition and streaming on.
- The probe fills the capability values that were `unverified`; a value the probe cannot confirm stays false.
- A model whose probe result lacks `tools` or `streaming` gets a "not usable as a Worker" warning in the dashboard on
  the model (a warning, not a block; the operator may still assign it). A Worker edits files through tools.
- The probe result is stored per model; it never reads a key from the registry and never prints it.

## 8. Sources

- [S1] Anthropic Messages API, https://platform.claude.com/docs/en/api/messages (the older `docs.anthropic.com/en/api/messages` redirects here).
- [S2] OpenAI OpenAPI spec, https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml (path `/chat/completions`, operationId `createChatCompletion`). The OpenAI reference pages returned 403/404 when fetched.
- [S3] Gemini OpenAI compatibility, https://ai.google.dev/gemini-api/docs/openai (base URL, `/chat/completions`, Bearer).
- [S4] Gemini generateContent, https://ai.google.dev/api/generate-content (endpoint; key as `?key=`).
- [S5] Amazon Bedrock Converse, https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html (path and `bedrock:InvokeModel`).
- [S6] Azure OpenAI REST reference, https://learn.microsoft.com/en-us/azure/foundry/openai/reference (chat path with `api-version`; `api-key` header).
- Repo: `litellm/config.yaml` (LiteLLM `openai/` prefix, `use_chat_completions_url_for_anthropic_messages`), `claude-runner/README.md` ("Providers per role"), `claude-runner/docs/roles-config.md`.

Prices are not sourced here; the operator sets them from the vendor price page.

## 9. Storage: where each value lives

The stores exist in the code; the database ones are read only with `providers_db` on. Names only, no values.

| Value | Where it lives | Holds | Written by |
|---|---|---|---|
| Provider and model rows | `board.providers`, `board.provider_models` (database, behind `providers_db`) | name, `api_type`, `base_url`, `status`, `source`, model fields from section 1 | dashboard Providers tab, through the store methods |
| Secret values (API keys, tokens) | secrets file at `CLAUDE_RUNNER_SECRETS_FILE` (section 10) | the key and token values, keyed by provider name | dashboard, once, through the store |
| Env blocks (today's mechanism) | container environment, `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` (see `.env.example`) | one provider block per name; read by `config.py` `_providers` | operator, in `.env` |
| LiteLLM routes | `litellm/config.yaml` (`model_name`, `litellm_params`) | the alias to upstream model mapping and the `os.environ/` references | operator, by hand |

The database rows never hold a secret value. A provider row names the secret by provider name only.

## 10. Secrets handling

Decision (operator, 2026-10-27): **encrypt at rest**. `cryptography` is a runtime dependency of `claude-runner`
(`pyproject.toml`). The keys and tokens are stored in the file at `CLAUDE_RUNNER_SECRETS_FILE`
(`/secrets/provider-secrets.json` on the volume `ahawr-provider-secrets`, shared by `claude-runner` and
`ahawr-dashboard`), encrypted with a Fernet master key from `CLAUDE_RUNNER_SECRETS_KEY` (mode 0600, atomic write).
The mode is switched on by `crypto.txt` next to the file, which holds the word `encrypted`; without it the file would be
plain JSON.

> **Back up the master key.** It lives only in the host `.env` (`CLAUDE_RUNNER_SECRETS_KEY`). Without it the stored provider
> keys cannot be read and must be entered again. The key is never written to the file, the database or the logs, and the
> Worker's CLI environment drops every `CLAUDE_RUNNER_*` variable, so the Worker does not receive it.

Limit: the Worker runs in the same container as the runner, so a process of the same user could read the runner's
environment. The env blocks of providers had the same limit before; it is not new.

Handling rules:

- `CLAUDE_RUNNER_SECRETS_FILE`: path of the secrets file. Mode 0600, owner the runner user. The runner refuses to read
  the file if the mode is wider.
- `CLAUDE_RUNNER_SECRETS_KEY`: the Fernet master key of the encrypted file. It lives in the environment only, never
  written to disk by the runner, never shown in the UI.
- Atomic write: write a temp file in the same directory, flush and fsync it, set mode 0600, then rename it over the
  secrets file. A crash leaves the old file intact.
- Redaction: the dashboard shows a secret as set or unset, never its value. Logs, the probe result (section 7), run
  reports and this doc carry no secret value and no full header that holds one.
- The API never returns a secret value. The write route takes the value once and stores it.

## 11. Flags: providers_db and litellm_sync

Both flags are OFF by default. With a flag off the behaviour is byte-for-byte unchanged (CLAUDE.md "Conventions").

| Flag | Turns on | Off means |
|---|---|---|
| `providers_db` | the `board.providers` and `board.provider_models` tables, the registry (section 3), the Providers tab | the env blocks are the only source; the database is not read |
| `litellm_sync` | writing the LiteLLM route entries from the database to LiteLLM (section 13) | `litellm/config.yaml` is hand-edited as today |

Override rule: with `providers_db` ON, a database provider overrides an env block of the same name. With it OFF, only
the env blocks are read, byte-for-byte as today. The dashboard shows which source a provider uses.

## 12. Reload

The runner's ProviderRegistry (`provider_registry.py`) reloads without a restart: it polls the table's `updated_at` every
`PROVIDERS_RELOAD_POLL_SECONDS` seconds (a named constant, value to be set in `config.py`, unit in the name) and also
reloads through a reload route that the dashboard calls after a save, `POST /api/board/providers/reload` (header
`X-AHAWR-Dashboard: 1`, CLAUDE.md, conventions). The poll stops when the Providers tab is left (the UI clears its
timer). `GET /v1/providers` returns the merged provider list without secrets. The env blocks are read at container start
only; a change to `.env` needs a container restart, which this doc does not request.

Migration from `.env` (proposal, read-only): a one-time read of the env block names and the non-secret fields
(`..._BASE_URL`, `api_type` if present) becomes database rows; per the override rule (section 11), with `providers_db`
ON a database row overrides an env block of the same name. Each row starts with status `unverified` until the probe
passes. Variables whose name ends in `_AUTH_TOKEN` or `_API_KEY` are NOT copied: the operator enters them once in the
dashboard, and they go to the secrets file. The migration never writes to `.env`, never prints a value, and is safe to run
twice (it skips existing rows).

## 12a. Activation steps done on 2026-10-27

1. The two tables of `board-schema.sql` created in the n8n database, and `GRANT SELECT, INSERT, UPDATE, DELETE` on them plus
   `GRANT USAGE, SELECT` on `board.provider_models_id_seq` to the role `board_operator` (the dashboard role; the schema
   file has no GRANT lines, a new table needs them).
2. `CLAUDE_RUNNER_PROVIDERS_DB=1` on `claude-runner` and `ahawr-dashboard` (`docker-compose.yml`); `litellm_sync` stays off.
3. The master key generated into the host `.env`, `crypto.txt` written into the volume, an encrypted round trip checked
   through the dashboard (a throwaway provider and a made-up key, deleted afterwards; no plaintext in the file).
4. The env blocks were NOT copied into rows: the dashboard container has no env blocks, and a row without a key
   would override the working block of the same name. Enter a provider in the Providers tab (the key goes to the
   encrypted file) and then remove its env block.

## 13. LiteLLM operator steps for litellm_sync

Not done here: this is a topology change (a database under LiteLLM) and needs operator approval (CLAUDE.md "Ask first").
Steps, for the operator to check against the pinned LiteLLM version before any change:

1. Give LiteLLM a database for its own key and spend tables: a `general_settings` entry that names the database by an
   `os.environ/` reference (the exact key name to be confirmed in the LiteLLM docs for the pinned version; not confirmed
   here).
2. Provide that connection string in the environment, never in `litellm/config.yaml`.
3. Keep `master_key` as it is (`os.environ/LITELLM_MASTER_KEY`).
4. Turn `litellm_sync` on only after step 1 has been tested on a copy of the stack.

Until then `litellm/config.yaml` stays as it is (section 5.2 shows the entry shape).

## 14. Test button cost note

The Test button runs the probe (section 7): a model list call and a one-token request with a tool definition. On a paid
provider that request is billed, and it counts against the provider's rate limit. The button therefore asks for confirmation
showing the provider name, and it does not run on a timer. Local providers (`localhost`, the operator's LiteLLM) cost
nothing extra.

## 15. What is NOT changed

- With both flags off nothing changes: the env blocks and `roles_json` keep their current meaning and
  `litellm/config.yaml` stays hand-edited.
- The n8n data tables are not touched. The `board.providers` and `board.provider_models` tables live in the `board`
  schema (`board-schema.sql`) and are created by the operator, not by the runner.
- No secret is read, written or printed by the docs, the logs or the API; a secret is shown as set or unset.
- No container is restarted or recreated by this feature.

## 16. Rollback

Turn both flags off (`providers_db`, `litellm_sync`). The runner then reads only the env blocks, as it does today, and
`litellm/config.yaml` is used as hand-edited. The `board.providers` and `board.provider_models` rows stay in the database
unused; dropping them is a separate, operator-approved step. The secrets file can stay or be removed by the operator; the
env blocks never depended on it. Because env blocks keep working with both flags off, rollback needs no migration back.
