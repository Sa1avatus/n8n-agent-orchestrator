# Codex executor

## Codex CLI 0.159.0 reference

Pinned version: `@openai/codex@0.159.0`. Every item below is marked **CONFIRMED** when the pinned package
(help output, bundled schema, or the native binary's own strings) or a local probe shows it, and **HYPOTHESIS**
when it is not shown. A CONFIRMED name does not mean its structure or behaviour is confirmed; that is noted per item.

### How this was obtained

Install outside the repository (the install is not kept; reproduce it with these commands):

```
npm install --prefix /tmp/codex-cli @openai/codex@0.159.0
/tmp/codex-cli/node_modules/.bin/codex --version            # -> codex-cli 0.159.0
/tmp/codex-cli/node_modules/.bin/codex exec --help          # saved to /tmp/codex-cli/exec-help.txt
/tmp/codex-cli/node_modules/.bin/codex exec resume --help   # saved to /tmp/codex-cli/resume-help.txt
/tmp/codex-cli/node_modules/.bin/codex app-server generate-json-schema --out /tmp/codex-cli/schema   # offline
```

Sources used below:

- `H-exec` / `H-resume` / `H-top`: `codex exec --help`, `codex exec resume --help`, `codex --help`.
- `S`: `/tmp/codex-cli/schema/codex_app_server_protocol.v2.schemas.json`, generated offline by the binary. It is the
  JSON-RPC app-server protocol and the `Config` (config.toml) definition. It is not the `codex exec --json` stream.
- `B`: strings of `/tmp/codex-cli/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex`
  (byte scan). These are serde names and messages compiled into the binary; they show that a name exists, not how it is used.
- `R`: local probes, no model reached. `codex exec --json` against `base_url = http://127.0.0.1:9/v1` (nothing listens)
  and an unset `env_key` variable. Logs: `/tmp/codex-cli/exec-fail2.jsonl`, `/tmp/codex-cli/exec-envkey.jsonl`.
- The package itself ships only `bin/codex.js`, a README and the platform binary. It has no TypeScript types, no JSON
  schema for `exec --json` events and no config.toml schema file.

### Exec flags (H-exec)

| Flag | Status | Meaning (help text) |
|---|---|---|
| `--json` | CONFIRMED | "Print events to stdout as JSONL" |
| `-o, --output-last-message <FILE>` | CONFIRMED | file where the last agent message is written (not exercised) |
| `--output-schema <FILE>` | CONFIRMED | JSON Schema file describing the final response shape (not exercised) |
| `-s, --sandbox <SANDBOX_MODE>` | CONFIRMED | values `read-only`, `workspace-write`, `danger-full-access`; the same values are the `sandbox_mode` enum in `S` |
| `-C, --cd <DIR>` | CONFIRMED | "use the specified directory as its working root" |
| `-c, --config <key=value>` | CONFIRMED | dotted path; the value is parsed as TOML, a non-TOML value is taken as a raw string |
| `--skip-git-repo-check` | CONFIRMED | allow running outside a Git repository |
| `-m, --model <MODEL>` | CONFIRMED | model the agent should use |
| `-p, --profile <CONFIG_PROFILE_V2>` | CONFIRMED | layers `$CODEX_HOME/<name>.config.toml` on the base config |
| `--ephemeral` | CONFIRMED | run without persisting session files |
| `--ignore-user-config` | CONFIRMED | do not load `$CODEX_HOME/config.toml` |
| `--ignore-rules` | CONFIRMED | do not load execpolicy `.rules` files |
| `--strict-config` | CONFIRMED | error on config fields this version does not know (see probe below) |
| `--color always\|never\|auto` | CONFIRMED | output colour |
| `--enable <F>` / `--disable <F>` | CONFIRMED | same as `-c features.<name>=true/false` |
| `-i, --image <FILE>...` | CONFIRMED | images attached to the prompt |
| `--add-dir <DIR>` | CONFIRMED | extra writable directories |
| `--worktree` | CONFIRMED | run in a new managed Git worktree |
| `--thread-source <SOURCE>` | CONFIRMED | source classification of new threads |
| `--oss`, `--local-provider lmstudio\|ollama` | CONFIRMED | open-source local provider |
| `--approve-for-me` | CONFIRMED | route approvals through automatic review in workspace-write |
| `--dangerously-bypass-approvals-and-sandbox` | CONFIRMED | skips prompts and sandbox; help says "EXTREMELY DANGEROUS" |
| `--dangerously-bypass-hook-trust` | CONFIRMED | runs hooks without persisted hook trust |
| `--approval-policy` (exec flag) | not listed | no such flag in exec help; approval comes from `-c approval_policy=...` (see config) |

Prompt: positional. With no prompt, or with `-`, the prompt is read from stdin. If stdin is piped and a prompt is
also given, stdin is appended as a `<stdin>` block (H-exec). Without a prompt and with an open stdin, exec prints
"Reading additional input from stdin..." and waits (R). Run it with `</dev/null` to avoid the wait.

Subcommands of `exec` (H-exec): `resume`, `fork`, `review`, `help`.

### Resume syntax (H-resume)

```
codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]
codex exec resume --last [OPTIONS] [PROMPT]
```

- `SESSION_ID`: a UUID or a thread name; a UUID takes precedence. `--last` picks the newest recorded session.
- `--all`: show all sessions, disabling the cwd filter.
- `PROMPT`: sent after resuming; `-` reads stdin.
- Resume accepts `-c`, `-m`, `-i`, `--json`, `-o`, `--output-schema`, `--ephemeral`, `--ignore-user-config`,
  `--ignore-rules`, `--skip-git-repo-check`, `--strict-config`, `--enable`/`--disable`, `--worktree`,
  `--thread-source`, `--dangerously-bypass-*` (H-resume).
- Resume does NOT accept `-s/--sandbox`, `-C/--cd` or `-p/--profile` (absent from H-resume). Use `-c sandbox_mode=...`
  (CONFIRMED that `-c` is accepted on resume). Whether a `-c sandbox_mode` on resume changes the sandbox of the
  resumed thread: HYPOTHESIS, not run.
- The working directory of a resumed session: HYPOTHESIS (cwd filtering is documented in H-resume, the cwd of the
  resumed thread is not).

### JSONL events (`codex exec --json`)

Event type names are CONFIRMED from the binary's `ThreadEvent` serde names (B):
`thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.started`, `item.updated`, `item.completed`, `error`.

Observed lines (R; the thread id is shortened):

```
{"type":"thread.started","thread_id":"01a18e07-…"}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"error","message":"Model metadata for `test-model` not found. Defaulting to fallback metadata; this can degrade performance and cause issues."}}
{"type":"error","message":"Missing environment variable: `NO_SUCH_VAR_FOR_PROBE`."}
{"type":"turn.failed","error":{"message":"Missing environment variable: `NO_SUCH_VAR_FOR_PROBE`."}}
```

Per event:

| Event | Fields | Status |
|---|---|---|
| `thread.started` | `type`, `thread_id` (UUID) | CONFIRMED (R) |
| `turn.started` | `type` only | CONFIRMED (R) |
| `turn.completed` | `type`, `usage` | `usage` names CONFIRMED in B: `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`. Placement inside the event: HYPOTHESIS, the success path was not run |
| `turn.failed` | `type`, `error.message` | CONFIRMED (R, missing env var). `error` may carry more fields: HYPOTHESIS |
| `error` | `type`, `message` | CONFIRMED (R). Used for retry notices: `Reconnecting... waiting for network (Connection failed: error sending request)` |
| `item.started` / `item.updated` / `item.completed` | `type`, `item` (object with `id`, `type`, type-specific fields) | CONFIRMED (R for `item.completed`) |

Item types (`item.type`). The names are CONFIRMED in B as the exec ThreadItem details variants:
`agent_message`, `reasoning`, `command_execution`, `file_change`, `mcp_tool_call`, `web_search`, `todo_list`, `error`
(R confirmed `error`). Also present in B: `context_compaction`, `user_message`, `function_call_output`, `hook_prompt`,
`dynamic_tool_call`, `collab_agent_tool_call`, `sub_agent_activity`, `image_view`, `entered_review_mode`,
`exited_review_mode`, `inter_agent_communication`. Which of these `exec --json` actually emits: HYPOTHESIS except `error`.

Item fields. The app-server ThreadItem variants in `S` (camelCase) show the shape; the exec JSON uses snake_case names
(`aggregated_output`, `exit_code` are in B). Placement and enum values in exec JSON are HYPOTHESIS:

- `command_execution`: `command`, `aggregatedOutput`/`aggregated_output`, `exitCode`/`exit_code`, `status`, `cwd`, `durationMs`
  (S: `commandExecution`).
- `file_change`: `changes`, `status` (S: `fileChange`).
- `mcp_tool_call`: `server`, `tool`, `arguments`, `result`, `error`, `status` (S: `mcpToolCall`).
- `web_search`: `query`, `action`, `results` (S: `webSearch`).
- `agent_message`: `text` (S: `agentMessage`). `reasoning`: `summary`, `content` (S: `reasoning`).
- `plan` (app-server name; `todo_list` in exec): `text` (S: `plan`). Exec `todo_list` item fields: HYPOTHESIS.
- `context_compaction`: `id` (S: `contextCompaction`).

Status words present in B: `in_progress`, `completed`, `failed`, `interrupted`, `pending_init`, `running`,
`errored`, `not_found`. The exact set per item: HYPOTHESIS. The app-server `TurnStatus` in `S` is
`completed`, `interrupted`, `failed`, `inProgress`.

Usage numbers in `S` (`TokenUsageBreakdown`, camelCase): `inputTokens`, `cachedInputTokens`, `cacheWriteInputTokens`,
`outputTokens`, `reasoningOutputTokens`, `totalTokens`. Exec `turn.completed` uses the snake_case names above: HYPOTHESIS for
`total_tokens` (not seen in B).

### Config keys (config.toml and `-c`)

`S` `Config` (25 top-level keys, `additionalProperties: true`) and the `--strict-config` probe (R) show which keys are real.
`--strict-config` rejects an unknown key with `unknown configuration field \`model_providers.localfail.bogus_key_zz\` in -c/--config override`
(R). The documented keys below passed strict validation in R (no unknown-field error). Their semantics are HYPOTHESIS unless
the status column says otherwise.

| Key | Status | Source |
|---|---|---|
| `model`, `model_provider`, `model_reasoning_effort`, `model_reasoning_summary`, `model_verbosity`, `review_model` | CONFIRMED name | S `Config` |
| `model_context_window` | CONFIRMED name, type integer | S `Config` |
| `model_auto_compact_token_limit` | CONFIRMED name, type integer | S `Config`; accepted in R |
| `model_auto_compact_token_limit_scope` | CONFIRMED name | S `Config` |
| `compact_prompt` | CONFIRMED name, type string or null | S `Config` |
| `experimental_compact_prompt_file` | CONFIRMED name in B only; semantics (a file path) HYPOTHESIS | B |
| `model_instructions_file` | CONFIRMED name in B only; path semantics HYPOTHESIS | B |
| `project_doc_fallback_filenames` | CONFIRMED name in B; a sequence of strings (R: a string value is rejected with `expected a sequence`); HYPOTHESIS on which names are read | B, R |
| `tool_output_token_limit` | CONFIRMED name in B and accepted in R; the unit and behaviour are HYPOTHESIS | B, R |
| `approval_policy` | CONFIRMED; values `untrusted`, `on-request`, `never` | S `AskForApproval`; `never` accepted in R |
| `sandbox_mode` | CONFIRMED; values `read-only`, `workspace-write`, `danger-full-access` | S `SandboxMode`; accepted in R |
| `sandbox_workspace_write` | CONFIRMED; fields `writable_roots`, `network_access`, `exclude_slash_tmp`, `exclude_tmpdir_env_var` | S `SandboxWorkspaceWrite` |
| `shell_environment_policy` | CONFIRMED name in B; `shell_environment_policy.inherit=all` is in the H-top help example; other sub-keys HYPOTHESIS | B, H-top |
| `permissions.<name>.filesystem` | CONFIRMED strings in B: `default_permissions`, `permissions profile … extends`, `filesystem`. The nested table shape is HYPOTHESIS | B |
| `hooks` | CONFIRMED name in B; events `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `SubagentStart`, `SubagentStop`, `Interrupt` (B). Table shape and trust file: HYPOTHESIS | B, H-exec |
| `model_providers.<id>` | CONFIRMED table. Fields in B's provider struct: `name`, `base_url`, `env_key`, `env_key_instructions`, `experimental_bearer_token`, `query_params`, `http_headers`, `request_max_retries`, `stream_max_retries`, `stream_idle_timeout_ms`, `websocket_connect_timeout_ms`, `requires_openai_auth`, `supports_websockets`, `model_catalog_url` | B; `name`, `base_url`, `wire_api`, `env_key`, `http_headers`, `request_max_retries`, `stream_max_retries`, `stream_idle_timeout_ms` accepted by strict parsing in R |
| `model_providers.<id>.wire_api` | CONFIRMED value `responses`; B says `wire_api = "chat"` is no longer supported | B |
| `model_providers.<id>.env_key` | CONFIRMED: an unset variable fails the turn with `Missing environment variable` | R |
| `model_providers.<id>.base_url` | CONFIRMED: a closed port gives the reconnect loop in R | R |
| `model_providers.<id>.request_max_retries` / `stream_max_retries` | CONFIRMED names; setting both to 0 did NOT stop the `Reconnecting…` loop within 45 s (R). Their effect: HYPOTHESIS | R |
| `model_providers.<id>.stream_idle_timeout_ms` | CONFIRMED name, accepted in R; units ms by name, the effect is HYPOTHESIS | B, R |
| `model_providers.<id>.http_headers` | CONFIRMED name; a map of string values, accepted in R | B, R |

Probe notes (R): every probe above used `--strict-config`, `--ignore-user-config`, `--ephemeral` and the fake
`localfail` provider. None sent a request to a model.

### Compaction

- Headless compaction command in `codex exec`: NOT present. `codex exec --help` lists only `resume`, `fork`, `review`,
  `help` (H-exec), and `codex --help` has no compact command (H-top).
- Automatic compaction in the runtime: present (B strings `run_auto_compact`, `run_pre_sampling_compact`,
  `auto_compact_fallback_prompt`, `auto_compact_fallback_buffer_tokens`, `model_post_turn_compact_threshold_percent`,
  `model_auto_compact_token_limit`). Whether `exec` compacts a long run automatically: HYPOTHESIS, not run.
- Programmatic compaction: the experimental app-server JSON-RPC method `thread/compact/start` with params
  `{"threadId": string}` and an empty response, plus the notification `thread/compacted` (S method list and
  `ThreadCompactStartParams`). This needs `codex app-server` running; it is not an `exec` option. Not exercised: HYPOTHESIS.
- Slash command `/compact` in `exec`: HYPOTHESIS. The names `clear` and `compact` appear with the hook names in B,
  but no exec documentation shows `/compact` being parsed as a prompt. Do not rely on it.
- Summary: a headless compaction exists only as the experimental app-server method. The pinned `codex exec` has no
  compaction command or flag.

### Errors as they appear

Error classes are CONFIRMED in `S` (`CodexErrorInfo`, camelCase, used by app-server `TurnError` and `ErrorNotification`):
`contextWindowExceeded`, `usageLimitExceeded`, `rateLimitExceeded`, `sessionBudgetExceeded`, `unauthorized`,
`badRequest`, `internalServerError`, `serverOverloaded`, `flexUnavailable`, `cyberPolicy`, `misalignmentPolicyViolation`,
`tooManyDenials`, `threadRollbackFailed`, `sandboxError`, `other`. Variants with `httpStatusCode`: `httpConnectionFailed`,
`responseStreamConnectionFailed`, `responseStreamDisconnected`, `responseTooManyFailedAttempts`.
`TurnError` is `{message, codexErrorInfo?, additionalDetails?, misalignment?}`; `ErrorNotification` adds `threadId`, `turnId`, `willRetry`.

How they reach `exec --json`:

| Case | Observed in exec JSON | Status |
|---|---|---|
| Missing env var (`env_key`) | `error` event and `turn.failed` with `error.message`; exit code 1 | CONFIRMED (R) |
| Connection refused, transient | repeated `error` events `Reconnecting... waiting for network (Connection failed: …)`; no `turn.failed` within 45 s | CONFIRMED (R). Final outcome after the retry budget: HYPOTHESIS |
| Unknown model | `item.completed` with `type: error` (`Model metadata … not found`), the run continues | CONFIRMED (R) |
| Context window exceeded | class `contextWindowExceeded` in `S`; exec text in B: `full_context_window_limit_reached`, `token_limit_reached`. Message and event shape: HYPOTHESIS | S, B |
| Rate limit / usage limit | classes `rateLimitExceeded`, `usageLimitExceeded` in `S`. Exec shape: HYPOTHESIS | S |
| Auth (401) | class `unauthorized` in `S`. Exec shape: HYPOTHESIS | S |
| HTTP 5xx | classes `internalServerError`, `serverOverloaded`, `responseStreamDisconnected {httpStatusCode}` in `S`. Exec shape: HYPOTHESIS | S |
| Whether `codexErrorInfo` / `httpStatusCode` appear in exec JSON | HYPOTHESIS: R showed only `message` | R |

### Not verified in this task

- No model request was sent, so these are unverified: a successful `turn.completed`, `item.*` payloads of a real run,
  `-o`, `--output-schema`, `resume`, `compact`, the context-window, rate-limit, auth and 5xx messages.
- The probes used the fake provider `localfail` at `127.0.0.1:9` and an unset `NO_SUCH_VAR_FOR_PROBE`. No API key was set or read.

## llama-server /v1/responses

Status: SUPPORTED in the source of the pinned build. Source reading only. No HTTP request was sent to llama-server, LiteLLM or any model endpoint. Runtime behaviour was not tested.

### Source version

- Commit `c060ca974c773c7c3d17fd1b66dc9d312bc292c0`, from `/d/OpenAIProjects/llama.cpp/Dockerfile.llama:6` (`ARG LLAMA_CPP_COMMIT=c060ca974c773c7c3d17fd1b66dc9d312bc292c0`). The build checks it out at `Dockerfile.llama:36` (`git checkout --detach "${LLAMA_CPP_COMMIT}"`), then applies five local patches at lines 37-41: `llama-cpp-dcfr-research`, `gdn-override-dev`, `layer-pass-lv4-tensor-usage`, `gdn-transactional-prompt-cache`, `mtp-window`.
- The commit object is in the local git store of `/d/OpenAIProjects/llama.cpp`, and its tree contains `tools/server`. Read-only extraction with `git archive c060ca97... tools/server common ggml/include` into `/tmp/llama.cpp`. No network fetch; the working tree of that repository was not touched.
- None of the five applied patches mentions `respons`, `v1/models`, `api_key` or `Bearer` (`grep -c -i respons` = 0 for each file). The largest hunk start in `gdn-transactional-prompt-cache.patch` is old-file line 1390, below the route code cited here (4859-4944).
- Link to the host server: `docker-compose.yml` has no llama.cpp service and no version. That the host llama-server is built from this Dockerfile is a HYPOTHESIS. See Risks.

### Route

`grep -rn '/v1/responses' tools/server/` in the pinned tree:

```
tools/server/server.cpp:247:    ctx_http.post("/v1/responses",             ex_wrapper(routes.post_responses_oai));
tools/server/server.cpp:267:    ctx_http.post("/v1/responses/input_tokens",        ex_wrapper(routes.post_responses_tok_oai));
tools/server/README.md:1455:### POST `/v1/responses`: OpenAI-compatible Responses API
```

- `tools/server/server.cpp:205`: in router mode `routes.post_responses_oai = models_routes->proxy_post;`. The entrypoint runs router mode (see Risks).
- `tools/server/server-context.cpp:4859-4875`: the handler converts the body with `server_chat_convert_responses_to_chatcmpl` (4862) and calls `handle_completions_impl` with `TASK_RESPONSE_TYPE_OAI_RESP` (4874).

### Streaming: confirmed (source)

- SSE framing: `tools/server/server-common.cpp:1603-1610` `format_oai_resp_sse` writes `event: <name>` and `data: <json>` per event. Used at `server-context.cpp:4280` and `4375` when the response type is `TASK_RESPONSE_TYPE_OAI_RESP`.
- Event names in `tools/server/server-task.cpp`: `response.created` (1171), `response.in_progress` (1182-1196), `response.output_item.added` (1210), `response.reasoning_text.delta` (1226), `response.content_part.added` (1251), `response.output_text.delta` (1264), `response.function_call_arguments.delta` (1293), `response.output_item.done` (616, 659, 678), `response.output_text.done` (627), `response.content_part.done` (643), `response.completed` (689).
- Stream flag: the Responses body keeps its own `stream` field (`server-chat.cpp:15`, copies the body and erases only `input`); it is read as a bool at `server-context.cpp:4220` (`bool stream = json_value(data, "stream", false);`) inside `handle_completions_impl` (defined at 4129).
- Not checked: the non-streaming output path, and the bytes on the wire.

### Tool calling: confirmed (source), function tools only

- Input items: `server-chat.cpp:163-177` converts `function_call` items; `server-chat.cpp:191-199` converts `function_call_output` items.
- Tools: `server-chat.cpp:252-277` keeps only `type == "function"`. Other tool types are skipped with a warning (`server-chat.cpp:257-265`, `SRV_WRN("unsupported Responses tool type ...")`).
- Output: `function_call` items (`server-task.cpp:571`, `671`, `1283`) and `response.function_call_arguments.delta` (`1293`).
- Limits: `previous_response_id` is rejected (`server-chat.cpp:10-12`, `llama.cpp does not support 'previous_response_id'.`). A Codex resume that relies on it will fail.

### Auth with the API key: confirmed (source) for the check; HYPOTHESIS for the host setting

- Key source: `common/arg.cpp:3457-3458` `--api-key`, a comma-separated list. Entrypoint: `docker-entrypoint.sh:976-978` adds `--api-key "$LLAMA_API_KEY"` only when `LLAMA_API_KEY` is non-empty.
- Middleware: `tools/server/server-http.cpp:206` (`middleware_validate_api_key`). With no keys it passes (208-210). Public paths skip the check (213): `/health`, `/v1/health` and the UI assets (`server-http.cpp:197-201`). Otherwise it reads `Authorization` (218), falls back to `X-Api-Key` (221), strips `Bearer ` (226-227), compares to the keys (231), and returns 401 `Invalid API Key` (236).
- The check runs as a pre-routing handler (`server-http.cpp:277`, `303`), so it covers `/v1/responses`.
- HYPOTHESIS: the host server runs with `LLAMA_API_KEY` set and equal to `LLAMACPP_API_KEY` (`docker-compose.yml:428`, default `none`). Not checked; `.env` was not read.

### GET /v1/models: confirmed (source)

- Route: `tools/server/server.cpp:240` `ctx_http.get ("/v1/models", ...)`. Handler `server-context.cpp:4944-4955` returns `get_res_models(*meta)`.
- Body: `server-context.cpp:4464` `{"object", "list"}` and 4465 `{"data", json::array({ get_res_model_info(meta) })}`.
- Per model (`server-context.cpp:4415-4428`): `id`, `object: "model"`, `owned_by: "llamacpp"`, `meta.n_ctx` (= `meta.slot_n_ctx`, line 4428). The `models` array also in the body (`get_res_models`) is an Ollama-style list.
- Not checked: whether the runner reads `meta.n_ctx`, or `--ctx-size`, from this field. That is a separate task.

### Risks and open points

- Port: `docker-entrypoint.sh:941` binds `--port 8033`. `docker-compose.yml:427` defaults `LLAMACPP_API_BASE` to `http://host.docker.internal:8080/v1`. The context probe uses 8033 (`docker-compose.yml:163`). Which port serves `/v1/responses` depends on `.env`, which was not read. Port 8080 may be a different server or build. HYPOTHESIS.
- Router mode: the entrypoint passes `--models-dir` and `--models-preset` (`docker-entrypoint.sh:939-944`). In router mode `/v1/responses` is proxied (`server.cpp:205`). The proxy at `tools/server/server-models.cpp:1914` was not read in full, so the child-side auth and handling in router mode are not confirmed.
- Reasoning event name `response.reasoning_text.delta` (`server-task.cpp:1226`). A client that expects another reasoning event name may ignore reasoning. Not checked against the OpenAI spec in this task.

### Acceptance status

- 1 (support, version, file:line): met. Supported in commit `c060ca97...`; route at `tools/server/server.cpp:247`.
- 2 (streaming, tool calls, auth): streaming confirmed (source); tool calls confirmed (source, function tools only); auth check confirmed (source); host key setting HYPOTHESIS.
- 3 (no request sent): met.

## Runner integration points

Read-only mapping of how ahawr-runner turns a run request into a Claude Code process. Source read at the
working tree; line numbers are from that read. No source file was changed.

| Point | file:function | Notes |
|---|---|---|
| Provider-block parsing | `ahawr-runner/src/ahawr_runner/config.py:_providers` (line 308) | `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>`; splits on the first `__`; builds `ProviderProfile` (`config.py:196`). Runner keys (`PROVIDER_RUNNER_KEYS`, `config.py:168`) are kept out of `ProviderProfile.env`. |
| Provider lookup per request | `config.py:Settings.provider` (line 608); `runs.py:RunManager._provider` (line 267) | `None` means the container env (no block). `extra_provider` hook for the board database (`runs.py:184`). |
| Role and provider reach the CLI | `runs.py:RunManager.start` (line 304) to `_launch` (line 378) to `_execute` (line 399) | `request.provider` and the role are passed through; `_execute` looks the provider up at line 446. |
| Command building | `ahawr-runner/src/ahawr_runner/claude_cli.py:build_command` (line 135); called from `runs.py:_execute` (line 486) | Argv: `-p --output-format stream-json`, `--model`, `--resume` or `--session-id` (line 155), permission, tools, MCP, `--settings`. |
| Environment building | `claude_cli.py:child_env` (line 231) | Container env minus `CLAUDE_RUNNER_*`; provider `env` overlaid; credentials removed when `ProviderProfile.isolates_credentials` (line 224). |
| Working directory | `runs.py:RunManager.resolve_cwd` (line 245); passed as `cwd=` to `create_subprocess_exec` (`runs.py:544`) | The run's cwd is resolved and mapped before launch. |
| Session storage | `runs.py:RunManager._bind` (line 350); `store.py:Store.bind_session` (line 115), `get_session` (line 108); `sessions` table (`store.py:28`) | Runner `session_id` to Claude `claude_session_id`; fresh-session retry path at `runs.py:320`. |
| Session resume | `runs.py:_bind` (line 365, 368); `claude_cli.py:transcript_exists` (line 34); `claude_cli.py:build_command` (line 155) | Resumes only if the transcript exists; otherwise a new id is bound. |
| Context probe (window) | `ahawr-runner/src/ahawr_runner/context_probe.py:probe_context` (line 65), which GETs `/v1/models` at line 68; `ctx_from_models` (line 44) | Reads `--ctx-size` (or `-c`) from `status.args` of the model; divides by `--parallel` unless unified KV. Called from `runs.py:_execute` (line 513) and `runs.py:compact` (line 890). |
| Autocompact trigger | `context_probe.py:autocompact_trigger` (line 103); `compact_pct` (line 90); `max_trigger` (line 83); `resume_compact_threshold` (line 108); `window_env` (line 119) | `window_env` gives the `CLAUDE_CODE_*` settings; `window_env` returns `None` below `MIN_TRIGGER_TOKENS`, so static values apply. |
| PreCompact instructions | `claude_cli.py:_compact_settings_path` (line 46) and `_compact_hook_command` (line 42); `_search_first_settings_path` (line 72) adds a `PreCompact` entry too; `build_command` (lines 190-203) decides | Delivered only when `local_compact_instructions` is on and the provider `isolates_credentials`. Text is `Settings.compact_instructions` (`config.py:430`, from `CLAUDE_RUNNER_COMPACT_INSTRUCTIONS`, `config.py:535`). |
| Compact run (manual `/compact`) | `runs.py:compact` (line 847); `runs.py:903-909` | Sends `/compact <instructions>` only for the local provider; otherwise `/compact`. |

### Proposed engine-switch seam

- Setting: per provider, the block key `CLAUDE_RUNNER_PROVIDER_<NAME>__ENGINE`, for example `CLAUDE_RUNNER_PROVIDER_CODEXLOCAL__ENGINE=codex`. Add `ENGINE` to `PROVIDER_RUNNER_KEYS` (`config.py:168`) so it stays out of `ProviderProfile.env`. Add the field `engine: str = "claude"` to `ProviderProfile` (`config.py:196-221`). `_providers` (`config.py:308`) validates it as `claude` or `codex` and raises `ValueError` otherwise, as `_opt_bool` does (`config.py:276`). No provider, or a provider without `ENGINE`, means `claude`.
- Seam: `runs.py:RunManager._launch` (line 378). The branch is `profile = self._provider(provider)` followed by `if profile is not None and profile.engine == "codex":`, which calls a separate codex execute path. Otherwise the existing `self._execute(...)` call (line 393) runs unchanged.
- Why this seam: `start` (line 304) and `_bind` (line 350) already run before `_launch`, so the codex branch receives the runner session and cwd; `build_command`, `child_env`, the context probe and the PreCompact code are not called on that path.
- Open points: the Codex branch needs its own session id mapping. Storing it in `sessions.claude_session_id` reuses the column without a schema change; a new column would be a schema change, which is an ask-first item. The codex branch also needs its own `_procs` and outcome handling, and `_finish` (line 637) must accept an engine-neutral outcome. This is a proposal only; no code was written.

## Event format and failure mapping

Read-only research (task T004). Every line number below was checked with `grep -n` or a read of that range.

### Path from stream to entry

- `StreamState.feed` (`claude_cli.py:304`) parses one stream-json line. It keeps the `result` event (`:319-321`), the last main-thread text (`:322-336`), the context size (`:337-352`), the last `api_retry` (`:353-354`) and the compaction (`:355-361`).
- `EventLog.feed` (`events.py:341-354`) passes each line to `LiveBlock.feed` (live view only, not stored, `events.py:163-189`) and to `StepTracker.feed` (`events.py:235-286`). It then calls `entries_from` (`events.py:60-154`). Each entry gets `seq` and `t` in `EventLog.add` (`events.py:330-339`), which appends it to `<data>/events/<run_id>.jsonl`.
- The runner writes some entries itself, not from the stream (`runs.py`): `prompt` (`:391`), `git_snapshot` (`:437`), `thinking_state` (`:443`, `:477`), `context_window` (`:521`), `qa_verdict` (`:684`), `end` (`:694`).
- `entries_from` adds `agent` to entries that come from a subagent (`events.py:151-153`). `EventLog.feed` adds `step` to `thinking`, `text` and `tool_use` entries of the main thread (`events.py:348-351`).

### Event kinds and the fields the dashboard reads

`dashboard.py` has no event-kind literals; it only serves `dashboard.html`. The reads are in `dashboard.html`.

| kind | written at | fields | dashboard.html reads |
|---|---|---|---|
| `prompt` | `runs.py:391` | `text`, `resume` | 865, 873, 880, 957, 988, 1013 |
| `init` | `events.py:102-112` | `model`, `cwd`, `permission_mode`, `tools`, `version` | 725, 869, 990, 1014 |
| `step` | `events.py:247-253`, `:277-282`, closed in `StepTracker._close` `:212-233` | `step`, `model`, `started`, `input_tokens`, `cache_read_tokens`, `cache_write_tokens`, `output_tokens`, `duration_ms` (`:218`), `ttft_ms` (`:220`), `generation_ms` (`:222`), `tokens_per_s` (`:232`), `stream_buffered` (`:229`) | 857-860, 891, 1052, 1120-1132 |
| `thinking` | `events.py:70-73` | `text` (`redacted_thinking` gives `[redacted by the provider]`), `step` | 724, 868, 991, 1015 |
| `text` | `events.py:74-75` | `text`, `step` | 992, 1016 |
| `tool_use` | `events.py:76-84` | `id`, `name`, `input`, `step` | 860, 884, 993, 1017, 1140 |
| `tool_result` | `events.py:88-97` | `id`, `is_error`, `text` | 722, 861, 994, 1022 |
| `user` | `events.py:98-101` | `text` | 989, 1013 |
| `retry` | `events.py:113-123` (`system`/`api_retry`) | `attempt`, `max_retries`, `status`, `error`, `delay_ms` | 958, 995, 1023 |
| `compact` | `events.py:124-135` (`system`/`compact_boundary`) | `trigger`, `pre_tokens`, `post_tokens`, `duration_ms` | 578, 927 (title), 996, 1024 (summary: trigger and token counts) |
| `result` | `events.py:136-150` | `subtype`, `is_error`, `text`, `num_turns`, `cost_usd`, `duration_ms`, `duration_api_ms`, `usage`, `model_usage` | 895, 997, 1025, 1160-1165 |
| `end` | `runs.py:691-700` | `status`, `error_code`, `error`, `stderr_tail` | 725, 869, 998, 1027, 1163 |
| `qa_scenario` | `qa_run_record.py:56-69` (`scenario_event`, added by `qa_run_record.py:140`) | `scenario`, `layer`, `verdict`, `reason`, `minutes` (only when set) | 999, 1026 |

Written but not read by `dashboard.html`: `git_snapshot` (`runs.py:437`), `thinking_state` (`runs.py:443`, `:477`), `context_window` (`runs.py:521`), `qa_verdict` (`runs.py:684`).

Mismatch: `dashboard.html` reads `step.approximate` (`:1052`, `:1120`, `:1128`, `:1132`), but no producer in `ahawr-runner/src` sets it. The only matches are `export.py:89` and `sources.py:222`, which are other producers. Every step is therefore shown as measured.

### Final result and cost

- The Claude Code result event supplies `total_cost_usd`. It is copied to `details["total_cost_usd"]` (`claude_cli.py:428`) and to the `result` entry's `cost_usd` (`events.py:144`).
- `_finish` (`runs.py:637`) turns the cumulative session counter into this run's share: `run_cost` (`runs.py:65`) with `store.session_cost_before` (`store.py:167-169`), at `runs.py:656`.
- A local-provider run with local pricing enabled replaces that cost with `details["local_cost"]["cost_usd"]` (`runs.py:657-666`).
- The stored value is `cost_usd` (`runs.py:674`), in the `runs` table column `cost_usd REAL` (`store.py:54`). The run list reads it at `run_views.py:74` and `runs.py:291`.
- The dashboard shows it as `$` with four decimals (`dashboard.html:1025`, `:1161`).

### Failure classes

The runner has no `transient` or `permanent` field. `classify` (`claude_cli.py:412-500`) sets a status, an error code, and an `http_code` that the Run Manager reads. The comment at `claude_cli.py:495` says the AHAWR retry logic reads the code and wording, so the transient/permanent decision is made outside these files.

| status | code | condition | file:line |
|---|---|---|---|
| `completed` | none | `subtype == "success"`, not `is_error` | `claude_cli.py:442-445` |
| `cancelled` | `cancelled` | the run was cancelled | `claude_cli.py:437-438` (`runs.py:610`) |
| `failed` | `timeout` (`http_code` 408) | the timeout fired | `claude_cli.py:439-441` (`runs.py:591-595`) |
| `failed` | `max_turns` | `subtype == error_max_turns` | `claude_cli.py:458-459` |
| `failed` | `max_budget` | `subtype == error_max_budget_usd` | `claude_cli.py:460-461` |
| `failed` | `session_not_found` | `SESSION_MISSING_RE` on stderr or text | `claude_cli.py:462-464`, `:20` |
| `failed` | `rate_limit` (429) | retry branch: `LIMIT_RE`, category, or status 429 | `claude_cli.py:465-473`, `:21`, `:25` |
| `failed` | `overloaded` (529) | retry branch: `OVERLOAD_RE`, category, or status 529 | `claude_cli.py:474-475`, `:22` |
| `failed` | `server_error` (500) | other transient status | `claude_cli.py:476-477` |
| `failed` | `terminal_reason` or `api_error` | any other `api_error_status` | `claude_cli.py:486-487` |
| `failed` | `cli_error` | no result line | `claude_cli.py:488-489`; stream read error `runs.py:613-615` |
| `failed` | `execution_error` | other non-success subtype | `claude_cli.py:490-491` |
| `failed` | `child_hung`, `orphan`, `qa_timeout` | watchdog | `runs.py:85-89`, `:616-620` |
| `interrupted` | `run_not_found` | runner shutdown | `runs.py:640-645` |
| `failed` | `cli_unavailable` | the CLI could not start | `runs.py:555` |

- `http_code` (`claude_cli.py:478-485`) is the upstream status when it is in `TRANSIENT_HTTP` (`claude_cli.py:26`), otherwise `RETRY_HTTP` (`claude_cli.py:25`).
- The rate-limit and overload wording is appended to the text when missing (`claude_cli.py:496-499`), so the retry matcher finds it.
- **Context overflow has no class.** Nothing in `ahawr-runner/src` matches `prompt is too long` or `overflow`. An overflow surfaces as an upstream 4xx and falls into `api_error` (`claude_cli.py:486-487`); its status is in `details["api_error_status"]` (`claude_cli.py:451`). Confirm the exact wording with a test before a class is added.

### Compact code path

- Endpoint: `api.py:512-517`. The route handler only calls `manager(request).compact(...)`. The body model is `CompactBody` (`api.py:169-172`: `mode` in `""|auto|always|off`, `min_tokens >= 0`, `model`).
- Logic: `RunManager.compact` in `runs.py:847-951`.
  - `mode == "off"` returns `compaction_disabled` (`runs.py:867-868`).
  - A busy session returns `session_busy` (`:870-871`). A missing session or transcript returns `session_not_found` (`:873-874`, `transcript_exists` at `claude_cli.py:34`).
  - The provider and model come from the session's last run (`:876-879`). With fresh-session retry on, the call returns `fresh_session_retry` (`:880-883`).
  - The threshold is the setting (`:851`), overridden by the provider (`:884-885`), then by the probed window (`:886-893`, `context_probe.py`).
  - In `auto` mode below the threshold it returns `below_threshold` (`:895-898`).
  - The compact prompt is `/compact` plus the instructions for the local provider only (`:899-909`).
  - The run is a record of kind `compact` with a `cmp_` id (`:910-922`), started with `compact=True` (`:923-930`). The result takes `pre_tokens` and `post_tokens` from the run's `compact` details (`:931-951`).
- CLI side: `build_command` takes `compact` (`claude_cli.py:142`). The PreCompact settings file is written by `_compact_settings_path` (`claude_cli.py:46-52`). The stream captures the compaction at `claude_cli.py:355-361`, and `classify` records it in `details["compact"]` (`claude_cli.py:434-435`). The status line is kept as `details["compact_result"]` (`runs.py:623-624`).
- **Where the logic lives:** `runs.py` (`RunManager.compact`), not `api.py`. `api.py` holds only the route and `CompactBody`. The only `api.py` change this mission allows, the version string at `api.py:308`, does not touch the compact code. The compact logic can be changed in `runs.py`, which this task listed as read-only.

### Not verified

- Behaviour was read from code, not run. No test was executed for this section.
- The `approximate` mismatch and the missing context-overflow class are findings, not fixes.

## Design

Task T005. Design only; no source file changed. Tags: CONFIRMED = shown by the T001/T002 evidence named in the
reference above; HYPOTHESIS = to be checked by a real `codex exec` run (the first implementation step) before it is relied on.

### 1. Event mapping (Codex JSONL to runner entries)

Runner entry kinds and fields are the ones in "Event kinds and the fields the dashboard reads" (T004). New kinds are marked NEW.

| Codex event or item | Runner entry | Fields | Status |
|---|---|---|---|
| `thread.started` | none; binds the session | `thread_id` goes to the session binding (section 9) | CONFIRMED (R) |
| `turn.started` | opens a `step` (StepTracker) | `started` | event CONFIRMED (R); step placement HYPOTHESIS |
| `turn.completed` `usage` | closes the `step` | `input_tokens` = input_tokens minus cached_input_tokens; `cache_read_tokens` = cached_input_tokens; `cache_write_tokens` = cache_write_input_tokens; `output_tokens`; `reasoning_output_tokens` (NEW field on `step`, not shown by the dashboard) | usage names CONFIRMED (B); placement HYPOTHESIS; the subtraction assumes input_tokens includes the cached part (HYPOTHESIS, Responses API semantics) |
| `item.started` `command_execution` | `tool_use` | `id` = item id, `name` = `Bash`, `input` = `{command}` | item shape HYPOTHESIS (S shape) |
| `item.completed` `command_execution` | `tool_result` | `id`, `is_error` = status failed or exit_code not 0, `text` = aggregated_output | HYPOTHESIS |
| `mcp_tool_call` | `tool_use` / `tool_result` | `name` = `mcp__<server>__<tool>`, `input` = arguments; result from `result`, or `error` with `is_error` | HYPOTHESIS |
| `file_change` | `tool_use` / `tool_result` | `name` = `file_change`, `input` = changes; `is_error` when status failed | HYPOTHESIS |
| `web_search` | `tool_use` / `tool_result` | `name` = `WebSearch`, `input` = query | HYPOTHESIS |
| `agent_message` (completed) | `text` | `text` | name CONFIRMED (B); field `text` CONFIRMED (S); exec shape HYPOTHESIS |
| `reasoning` | `thinking` | `text` = summary, else content | HYPOTHESIS |
| `error` item (for example unknown model) | `notice` (NEW kind; the dashboard does not read it yet) | `text` = message | CONFIRMED (R) |
| `error` event | `retry` | `error` = message; `attempt`, `max_retries` absent | CONFIRMED (R); the text is shown only, no status is derived from it |
| `context_compaction` item | `compact` | `trigger` = `auto`; `pre_tokens`, `post_tokens` null | HYPOTHESIS that exec emits it |
| `turn.failed` | failure path (section 3) | `error.message`, codexErrorInfo if present | CONFIRMED (R) |
| `item.updated` | live view only, not stored | - | pattern as `LiveBlock` (events.py:163), CONFIRMED in T004 |
| `todo_list` and other item types | no entry | - | HYPOTHESIS which are emitted |

Rules: an unknown event or item type is skipped and never raises; the parser keeps the line count of skipped types in the
run details.

### 2. Final message source

- Primary: `-o <run dir>/last_message.txt` (CONFIRMED flag, H-exec; not exercised). Read once after the process exits and
  stored as the run result text.
- Fallback: the text of the last `agent_message` item in the stream, used when the file is missing or empty.
- HYPOTHESIS: the `-o` file is written on success only; the fallback covers failed runs.

### 3. Failure mapping

Match order, first hit wins:

1. `codexErrorInfo` on `turn.failed.error` or the `error` event. HYPOTHESIS for exec; the enum values are CONFIRMED in S (camelCase). Accept the snake_case form too.
2. `httpStatusCode` when present. HYPOTHESIS; not seen in R.
3. Message text, and only for messages seen in R or present as strings in B. Message matching is the fallback, never the first choice.

No field and no matching text, with exit code not 0 and no `turn.failed`: `cli_error` (as today).

| Class | Match | Runner status and code | Retried |
|---|---|---|---|
| transient | `rateLimitExceeded`, HTTP 429 | failed, `rate_limit` | yes; the retry matcher must accept the code (claude_cli.py:495 says the retry logic reads code and wording) |
| transient | `serverOverloaded`, HTTP 529 | failed, `overloaded` | yes |
| transient | `internalServerError`, `responseStreamDisconnected`, `httpConnectionFailed`, `responseStreamConnectionFailed` | failed, `server_error` | yes |
| transient (notice) | `error` event `Reconnecting...` (CONFIRMED R) | `retry` entry, not a failure | the internal reconnect loop is not bounded by retries 0 (R, 45 s), so the run timeout bounds it |
| permanent | `unauthorized` | failed, `auth_error` (NEW code) | no |
| permanent | `badRequest` | failed, `api_error` | no |
| permanent | message `Missing environment variable` (CONFIRMED R) | failed, `config_error` (NEW code), before any request | no |
| permanent | `usageLimitExceeded` | failed, `usage_limit` (NEW code) | no (HYPOTHESIS for the policy) |
| context overflow | `contextWindowExceeded`; message `full_context_window_limit_reached` or `token_limit_reached` (B strings, exec shape HYPOTHESIS) | failed, `context_overflow` (NEW code) | once, after the digest handoff of section 9 |
| other | `sandboxError` | failed, `sandbox_error` (NEW code) | no |

The NEW codes exist only in the Codex mapping. The Claude classifier (`claude_cli.py:412-500`) is not changed.

### 4. Sandbox per role

| Role | Flags (initial call) | Writable | Network |
|---|---|---|---|
| architect | `-s read-only` | none | not set |
| reviewer | `-s read-only` | none | not set |
| worker | `-s workspace-write`, `-C <mission folder>`, `-c sandbox_workspace_write.writable_roots=["<mission folder>"]`, `-c sandbox_workspace_write.exclude_slash_tmp=true`, `-c sandbox_workspace_write.exclude_tmpdir_env_var=true`, `-c sandbox_workspace_write.network_access=false` | the mission folder only | off |
| service, qa, report | `read-only` (design default; the task names only three roles) | none | not set |

- All roles: `-c approval_policy="never"` (exec cannot prompt; accepted in R). Not a setting; fixed in code.
- Values and field names: CONFIRMED (S `SandboxMode`, `SandboxWorkspaceWrite`; `-s` in H-exec).
- HYPOTHESIS: whether `writable_roots` replaces or adds to the default writable roots; the `exclude_*` and `network_access=false` effects.
- Resume: `-s` and `-C` are not accepted by `codex exec resume` (H-resume); the workspace root comes from the process cwd
  (`create_subprocess_exec(cwd=)`).
- **Implemented 2026-11-05 (verified in the container, Codex 0.159.0):** neither command passes `-s` or `-c sandbox_mode`.
  An explicit sandbox on the command line makes Codex ignore the permissions profile of section 5, so the `.env` denies stop
  working. `sandbox_mode` (and the profile) come from the run's `config.toml`.
- **Container prerequisite:** the sandbox is bubblewrap, which needs user namespaces. The default Docker seccomp profile
  blocks them (`bwrap: No permissions to create a new namespace`). The `ahawr-runner` service in `docker-compose.yml`
  therefore sets `security_opt: seccomp=./data-tables/ops/seccomp/ahawr-runner-userns.json`: the Docker default profile
  (moby `profiles/seccomp/default.json`) plus `unshare`, `setns`, `clone`, `clone3`, `mount`, `umount2`, `pivot_root`.
  Checked: with it `bwrap` runs and a write outside the workspace fails with `Read-only file system`; without it `codex sandbox`
  fails. The kernel (WSL2) allows unprivileged user namespaces, so `SYS_ADMIN`, `apparmor` and `systempaths` are not needed.

### 5. `.env` deny rule

- Rule: no role reads `.env`, `.env.local` or `.env.*.local` under the mission folder. `.env.example` stays readable.
- **Implemented and verified 2026-11-05.** `default_permissions = "ahawr"` and, in `config.toml`:

```
[permissions.ahawr.filesystem]
":root" = "read"
":project_roots" = "write"        # "read" for every role but worker
"<workspace>/**/.env" = "none"
"<workspace>/**/.env.local" = "none"
"<workspace>/**/.env.*.local" = "none"
```

- Facts found by trying it: a glob key must start with an absolute path (a relative `**/.env` is rejected: "failed to load
  workspace requirements"); a glob key supports only `none` (`.env.example` cannot be allowed by a glob, so it is not listed
  and an `.env.*` deny would hide it too); `":root"` and `":project_roots"` are the special keys; a profile without `":root"`
  makes the sandbox unable to start `codex` itself. A denied file reads as an empty mode-0000 file or `Permission denied`.
- The hard protection stays section 6: the secrets are not in the shell's environment.

### 6. shell_environment_policy with no secrets

```
[shell_environment_policy]
inherit = "core"
include_only = ["PATH", "HOME", "LANG", "TERM"]
exclude = ["*KEY*", "*TOKEN*", "*SECRET*", "*PASSWORD*"]
```

- Status: the name is CONFIRMED (B; `inherit = "all"` in H-top). **Verified 2026-11-05: `inherit = "none"` drops `PATH` (the shell
  then finds no `ls`/`cat`); `"core"` keeps `PATH` and, with `include_only` and `exclude`, passes only the four names.**
  The pinned schema has no typed `shell_environment_policy` key (`grep -c` = 0 in the v2 schema), so the check is a
  `--strict-config` probe: an unknown sub-key is rejected (R).
- The Codex process itself receives the one API key variable (section 7), because it must read it for `env_key`. Shell
  children receive none of it. The Codex process env is built from `child_env` (claude_cli.py:231), which already strips
  `CLAUDE_RUNNER_*` and, with `isolates_credentials`, the credentials.

### 7. config.toml template

Written per run to `<run dir>/codex/config.toml`, selected with `CODEX_HOME=<run dir>/codex` (CONFIRMED, H-exec: "`$CODEX_HOME/<name>.config.toml`"). The runner owns that directory, so `--ignore-user-config` is not used.

OpenAI provider:

```
model = "<model from the role config>"
model_provider = "openai"
sandbox_mode = "<from section 4>"
approval_policy = "never"
model_auto_compact_token_limit = <from section 9, only when the window is probed>

[shell_environment_policy]          # section 6
inherit = "none"

[model_providers.openai]
name = "OpenAI"
base_url = "<CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_BASE_URL>"
env_key = "<CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_API_KEY_VAR>"
wire_api = "responses"
stream_idle_timeout_ms = <CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_STREAM_IDLE_TIMEOUT_MS>
```

Local llama-server:

```
model_provider = "llamacpp"

[model_providers.llamacpp]
name = "llama-server"
base_url = "http://host.docker.internal:8033/v1"
wire_api = "responses"
stream_idle_timeout_ms = 300000
```

- `wire_api = "responses"` CONFIRMED (B; `chat` is rejected). `/v1/responses` on llama-server SUPPORTED in source (T002).
- `base_url` in the local block is the value the task gives; the port is an open point (section 10).
- The local block has no `env_key`: an unset variable fails the turn (CONFIRMED R). Omitting it is HYPOTHESIS for a custom
  provider. If the host server runs with `LLAMA_API_KEY`, set `CODEX_API_KEY_VAR` and the matching key variable.

### 8. Price table and cost

- Setting: `CLAUDE_RUNNER_PROVIDER_<NAME>__PRICE_USD_PER_MILLION_TOKENS`, value `in,out,cache_read,cache_write`, USD per
  1,000,000 tokens. The parser is `parse_rates` (local_pricing.py:32), reused. Default: empty, which means no price and
  `cost_usd` is null. The local provider sets `0,0,0,0`.
- Formula, per `turn.completed` (usage fields CONFIRMED in B):

  ```
  uncached = max(input_tokens - cached_input_tokens, 0)
  turn_cost_usd = (uncached * in + cached_input_tokens * cache_read
                   + cache_write_input_tokens * cache_write + output_tokens * out) / 1_000_000
  run cost_usd = sum of turn_cost_usd over the run's turn.completed events
  ```

- HYPOTHESIS: `reasoning_output_tokens` is already inside `output_tokens` and is not added again. HYPOTHESIS: `usage` is per
  turn; if a later turn reports a running total, the cost uses the difference from the previous turn.
- Shortcut: one rate set per provider block, so a provider that serves two models at different prices gets a per-model key.
  Upgrade when such a provider exists.
- The stored value is `runs.cost_usd` (T004), set directly by the Codex branch.

### 9. POST /v1/sessions/{id}/compact for a Codex session

Decision: no headless compaction. The fallback is a digest handoff.

- Why not real compaction: T001 found no compaction command or flag in `codex exec` (H-exec, H-top). The only programmatic
  method is the app-server `thread/compact/start` (CONFIRMED in S), and it needs a second long-running `codex app-server`
  process per session. It was not exercised. That is outside this change, so the endpoint does not use it.
- Chosen behaviour: the compact call starts a new Codex thread with no `resume`. The prompt is built from the session's
  stored events with no model call, but the compact run itself is one Codex turn on that new thread, which answers `OK`.
  The prompt is capped at `CLAUDE_RUNNER_RETRY_INPUT_CHARS` (default 32000 characters, config.py:182).
  Response: the usual compact result with `pre_tokens` and `post_tokens` null. The session id changes.
- Implemented in `RunManager.compact` (`runs.py`, Codex branch). The prompt is `CODEX_HANDOFF_PREAMBLE`, then
  `TASK:` with the session's first prompt cut to `CODEX_HANDOFF_TASK_CHARS` (2000), then `session_digest` (`handoff.py`).
  The run keeps the session's current thread id with no resume. `codex_run.execute` stores the new thread id when the run
  captures one, for compact runs too. A failed compact leaves the session resumable on its old thread.
- `context_tokens` is never set for a Codex session: a Codex run passes no stream state to `_finish`, so the column keeps
  its default of 0 (`store.py:133`). With `mode=auto` the threshold is never reached, and only `mode=always` triggers a
  Codex compact (unless the threshold is 0).
- The context-overflow class of section 3 uses the same handoff, once per run.
- Automatic compaction: `model_auto_compact_token_limit` (CONFIRMED name, S) is set to the probed window times the existing
  provider key `COMPACT_PCT` (`context_probe.py:compact_pct`, line 90), in tokens. It is omitted when the probe fails.
  HYPOTHESIS: exec applies it (auto-compaction is present in B; the exec behaviour was not run).
- Reversal: if T001's app-server path is adopted later, it replaces the handoff for this endpoint only. That needs its own task.

### Settings (all new; Codex only)

| Key | Default | Unit | Read by |
|---|---|---|---|
| `CLAUDE_RUNNER_PROVIDER_<NAME>__ENGINE` | `claude` | enum `claude` or `codex` | `_providers` validates it, as `_opt_bool` does (config.py:268); `_launch` branch |
| `CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_BASE_URL` | `https://api.openai.com/v1` | URL | config.toml `base_url` |
| `CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_API_KEY_VAR` | `OPENAI_API_KEY` | name of an environment variable; empty omits `env_key` | config.toml `env_key` |
| `CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_STREAM_IDLE_TIMEOUT_MS` | `300000` | milliseconds | config.toml `stream_idle_timeout_ms` (effect HYPOTHESIS) |
| `CLAUDE_RUNNER_PROVIDER_<NAME>__PRICE_USD_PER_MILLION_TOKENS` | empty (no price) | USD per 1,000,000 tokens, `in,out,cache_read,cache_write` | section 8 |

- Add `ENGINE`, `CODEX_BASE_URL`, `CODEX_API_KEY_VAR`, `CODEX_STREAM_IDLE_TIMEOUT_MS` and `PRICE_USD_PER_MILLION_TOKENS` to
  `PROVIDER_RUNNER_KEYS` (config.py:168), so they stay out of `ProviderProfile.env`. The API key variable itself is a
  provider block entry, `CLAUDE_RUNNER_PROVIDER_<NAME>__OPENAI_API_KEY`, which reaches the Codex process only. Its value is
  never printed or copied into a report.
- Reused, not new: `COMPACT_PCT`, `CLAUDE_RUNNER_RETRY_INPUT_CHARS`, `parse_rates`.
- Fixed in code, not settings: the sandbox table (section 4) and `approval_policy`.
- Naming (docs/naming.md): all keys are `UPPER_SNAKE` with the `CLAUDE_RUNNER_PROVIDER_<NAME>__` prefix. Abbreviations are
  only `URL` and `MS`, both on the list. `MTOK` was avoided for `MILLION_TOKENS`, and `ENV` for `API_KEY_VAR`.

### Claude Code path unchanged when ENGINE is not set

- The only change in the run path is one branch in `RunManager._launch` (runs.py:378): `profile = self._provider(provider)`,
  then `if profile is not None and profile.engine == "codex":`. Without a provider block, or with `ENGINE` unset, the
  existing `self._execute(...)` call runs unchanged.
- New code goes in new modules named by area: `codex_cli.py` (argv, env, config.toml), `codex_events.py` (JSONL to entries,
  failure mapping), both with tests in `test_codex_cli.py` and `test_codex_events.py`.
- `_finish` (runs.py:637) is not changed. The Codex branch passes it the same outcome shape.
- `build_command`, `child_env`, `StreamState`, `classify`, the context probe and the PreCompact code are not called on the
  Codex path, and are not edited.
- Risk: `ProviderProfile` gains one field (`engine`). `dataclasses.asdict` output changes, and `tests/test_config_cli.py`
  imports `asdict`. That check is a test update, not a change to the Claude process argv, env or events.
- Risk: the Codex branch stores the thread id in `sessions.claude_session_id`. The column name is misleading. A rename is a
  DB schema change and needs approval.

### 10. Open points

- Every HYPOTHESIS above must be checked by one real `codex exec --json` run against llama-server and one against the
  OpenAI provider before the mapping code is relied on. No model request has been sent so far (T001-T004).
- The llama-server port: `docker-compose.yml` defaults `LLAMACPP_API_BASE` to 8080, and the context probe uses 8033 (T002
  risks). The base URL in section 7 uses 8033, as the task states; confirm which port serves `/v1/responses`.
- The `.env` rule cannot be proven as a read boundary for a read-only sandbox (section 5).

## Runner event format

Written by `ahawr-runner/src/ahawr_runner/codex_events.py` (`CodexEventMapper.feed`). The entries use the kinds and fields of `events.py`, so `dashboard.html` shows a Codex run as it shows a Claude run (the reads are listed in "Event kinds and the fields the dashboard reads" above). Lines that are not JSON, and event or item types that are not mapped, are logged and skipped; they never raise.

| Codex line | Runner entry |
|---|---|
| `thread.started` | none; `thread_id` becomes the session id |
| `turn.started` | none; starts the step clock |
| `turn.completed` | `step` with `step` (the turn number), `started`, `duration_ms`, `input_tokens` (usage input minus cached input), `cache_read_tokens`, `cache_write_tokens`, `output_tokens`; `model` is `""` |
| `turn.failed` | none; kept and classified after the process exits (see Failure classes) |
| `item.started` / `item.completed`, types `command_execution`, `mcp_tool_call`, `file_change`, `web_search` | `tool_use` (`Bash`, `mcp__<server>__<tool>`, `file_change` or `WebSearch`), emitted at start, or at completion when no start was seen; `tool_result` at completion with `is_error` and `text` |
| `item.completed`, `agent_message` | `text`; also becomes the final message |
| `item.completed`, `reasoning` | `thinking` with the summary or content text (empty text is dropped) |
| `item.completed`, `error` | `notice` |
| `item.completed`, `context_compaction` | `compact` with `trigger: "auto"` and null token fields |
| `error` | `retry` with `attempt`, `max_retries`, `status` and `delay_ms` null, and `error` text |

- `step` numbers are added to `text`, `thinking` and `tool_use` entries while a turn is open (`_entry`), as `events.py` does for Claude.
- Token totals for the run's cost come from the summed `turn.completed` usage (`CodexEventMapper.usage`); the price table turns them into USD (`codex_cost.py`).
- Items are clipped with the same limit as Claude entries (`events._clip`).

Failure classes (`classify_failure`, `codex_events.py`): a `turn.failed` error is matched by `codexErrorInfo` (for example `rateLimitExceeded` is transient, `unauthorized` is permanent, `contextWindowExceeded` is `context_overflow`), then by HTTP status (429 and 529 are transient), then by message text (`missing environment variable` is a config error). Anything else is `cli_error`, and so is a non-zero exit with no `turn.failed`.

## Known gaps

- No `codex exec --json` run has been made against llama-server or the OpenAI provider (docs T001-T004). Every item marked HYPOTHESIS in sections 3-7 and 9 is unverified: the writable roots and excludes, the `shell_environment_policy` and permission-profile sub-keys, the effect of `stream_idle_timeout_ms`, whether exec applies `model_auto_compact_token_limit`, and whether a resumed thread keeps the sandbox.
- The llama-server port is open: `docker-compose.yml` defaults `LLAMACPP_API_BASE` to 8080, while the examples use 8033 (section 10).
- `context_overflow` is classified (`codex_events.py`), but no code path starts the digest handoff on it.
- The Codex path never sets `context_tokens` (`codex_run.py` passes no stream state), so `mode=auto` compaction does not trigger; `mode=always` does.
- Step entries carry `model: ""` and no time-to-first-token or generation timing; a Codex run has a cost estimate only when the provider has a price.
- `sessions.claude_session_id` holds the Codex thread id. The name is misleading, and a rename is a schema change (ask first).
- `docker-compose.yml` does not pass `INSTALL_CODEX_CLI`, so a rebuild without the build argument removes the Codex CLI from the image.
- The `.env` read rule is best effort: a read-only sandbox does not by itself stop reads, so the hard protection is that the key is not in the shell's environment.

## Subscription login

Scope: how Codex signs in with a ChatGPT subscription, as far as the in-scope sources show. `codex` is not installed on
this host: `codex --version` and `codex login --help` both return `command not found`, so no CLI help text was read.
Every statement below is either backed by a named source or labelled HYPOTHESIS.

### What the repository shows

- `CODEX_HOME` is set for every Codex child process to a per-session directory: `codex_cli.py:78`
  (`env[CODEX_HOME_VAR] = str(codex_home)`), with `CODEX_HOME_VAR = "CODEX_HOME"` at `codex_cli.py:16`. CONFIRMED (source).
- The directory is `<data_dir>/codex-sessions/<first 32 hex of sha256(session id)>/codex`: `codex_run.py:54-57`
  (`session_dir`), `codex_run.py:74-76` (`home_root`, `write_codex_home`), `codex_config.py:15` (`CODEX_HOME_DIR_NAME = "codex"`).
  CONFIRMED (source).
- The directory is kept across the session's runs (`codex_run.py:73` comment). `write_codex_home` (`codex_config.py:111-115`)
  writes only `config.toml` and deletes nothing, so any other file Codex writes there would survive to the next run.
  CONFIRMED (source, read of the code; no run made).
- The child environment is a whitelist: `PATH`, `HOME`, `LANG`, `TERM` (`codex_config.py:20`, applied at `codex_cli.py:74`),
  plus the provider's one API-key variable (`codex_cli.py:75-77`). A ChatGPT login token in the runner's environment is
  therefore not passed to Codex. CONFIRMED (source); HOME is passed, so a `~/.codex` default would matter only if Codex
  ignored `CODEX_HOME` (HYPOTHESIS).
- No in-scope file reads, copies, mounts or names `auth.json`, a login flow or a refresh. `grep -rn -i "auth\.json\|device\|chatgpt\|subscription\|login\|refresh"`
  over `docs/codex-executor.md`, `codex_cli.py`, `codex_run.py`, `tests/fake_codex.py` and `codex_config.py` returned no
  matches. CONFIRMED (grep).
- The test double has no login: `tests/fake_codex.py:98-103` answers only `--version` and `exec`; any other command exits 2
  with `unsupported command`. No test covers `auth.json`, device login or token refresh. CONFIRMED (source).
- An auth failure is a permanent error: class `unauthorized` maps to `auth_error` (`docs/codex-executor.md:450`, and
  `classify_failure` in `codex_events.py` as described at line 649). The exec message shape for a 401 is HYPOTHESIS
  (line 207).

### Codex CLI behaviour (not verified)

- Location of the login file: `$CODEX_HOME/auth.json`. HYPOTHESIS. No in-repo source names it; the doc's binary strings
  (`B`) and help captures (`H-top`, `H-exec`) do not list it.
- Login without a browser (container): a device-code flow. HYPOTHESIS. The flag or subcommand name is not known from any
  in-scope source; check it with `codex login --help` on a host with `@openai/codex@0.159.0` (install block under
  "How this was obtained", line 9 onward). Do not run `codex login` in the runner's containers or as a Worker.
- Token refresh and write-back: Codex uses a stored refresh token to renew the access token and writes the new tokens back
  to `$CODEX_HOME/auth.json`. HYPOTHESIS. If true, the refreshed file lives in the per-session home under `data_dir`
  (see above), so each session holds its own copy, and a refresh-token rotation would invalidate any copy kept elsewhere.
- Whether a headless `codex exec` run refreshes an expired token on its own, and what it prints if it cannot: HYPOTHESIS.
  The only related shape is the `unauthorized` class (line 207).

### Open checks before the runner depends on a subscription login

1. On a host with the pinned CLI, save `codex --help` and `codex login --help` outside the repository and confirm the
   login flag, the file name and the home it uses. Output stays out of Git and reports.
2. Observe a refresh by the modification time of `auth.json` only; do not print the file. The login uses the operator's
   account, so the operator performs it.
3. Choosing where the login file lives (the per-session home as now, or one operator-staged home) changes authentication
   and persistent data. CLAUDE.md lists both as ask-first; no code change is made here.

### Subscription login: design

Design only. No code, settings file or compose file is changed by this section. The new setting is OFF by default
(`CODEX_AUTH=api_key`), so the current behaviour stays byte-for-byte unchanged.

**Where the API key is required today**

- The provider block holds the key under its variable name, `CLAUDE_RUNNER_PROVIDER_<NAME>__<KEY_VAR>`.
  `config.py:411-413` reads the variable name into `codex_api_key_var`.
- `codex_config.py:98-99` writes `env_key = ...` into `config.toml`, so Codex takes the key from that variable.
- `codex_cli.py:75-77` copies the key into the child environment, the only place it is passed. Nothing reads an
  `auth.json` or a login token.
- The per-run `CODEX_HOME` is created at `codex_run.py:74-76` and is not removed. Only the `-o` last-message file is
  deleted, by `_take_last_message` at `codex_run.py:271`.

**Functions to change (file:line)**

1. `config.py:173-186` `PROVIDER_RUNNER_KEYS`: add `CODEX_AUTH`, so it stays out of `ProviderProfile.env`.
2. `config.py:191`: add `DEFAULT_CODEX_AUTH = "api_key"` next to `DEFAULT_CODEX_API_KEY_VAR`.
3. `config.py:241`: new field `codex_auth: str = DEFAULT_CODEX_AUTH` on `ProviderProfile`.
4. `config.py:318`: new validator `_codex_auth(name, raw)`, modelled on `_codex_api_key_var` (and `_engine`), called in
   `_providers` at `config.py:411-413`.
5. `config.py:498` (`data_dir`) and `config.py:623` (`Settings.from_env`): new runner-wide `codex_auth_dir`, read from
   `CLAUDE_RUNNER_CODEX_AUTH_DIR`.
6. `codex_config.py:98`: write `env_key` only when `codex_auth == "api_key"`. `codex_cli.py:75` gets the same condition.
7. `codex_run.py:74-76` (`execute`): when `codex_auth == "chatgpt"`, copy the stored `auth.json` into the run's
   `CODEX_HOME` under the lock before the Codex process starts, and copy it back after the process exits.
8. New module `ahawr-runner/src/ahawr_runner/codex_auth.py` (name follows the `codex_*` family): the lock, the
   copy-in and the copy-out. No change to `child_env` (`codex_cli.py:72-79`): the file reaches Codex through `CODEX_HOME`,
   which is already passed.

**Settings (names)**

| Key | Values and default | Scope | Follows |
|---|---|---|---|
| `CLAUDE_RUNNER_PROVIDER_<NAME>__CODEX_AUTH` | `api_key` (default) or `chatgpt` | provider block | `ENGINE`, `CODEX_API_KEY_VAR` |
| `CLAUDE_RUNNER_CODEX_AUTH_DIR` | no default; absolute path; required when a provider sets `chatgpt` | runner-wide | `CLAUDE_RUNNER_DATA_DIR`, `CLAUDE_RUNNER_SECRETS_FILE` |

Any other `CODEX_AUTH` value raises `ValueError` at load, as for `ENGINE`.

The runner rejects `CLAUDE_RUNNER_CODEX_AUTH_DIR` when it lies under an allowed root or under
`<data_dir>/codex-sessions`, so the stored login is outside the repository and outside every run directory.
`CODEX_AUTH` is a full word and needs no abbreviation (docs/naming.md, section 3).

**Lock (standard library only)**

- `os.open(<auth dir>/auth.lock, O_CREAT | O_EXCL | O_WRONLY)` takes the lock; `os.unlink` releases it in a `finally`.
  `O_EXCL` works on the Linux container and on the Windows host; `fcntl` does not exist on Windows, so it is not used.
- The lock is held only during the copy-in and the copy-out, not during the run. A run can last minutes, and holding
  the lock for it would serialise every subscription run.
- Constants, with units in the names: `AUTH_LOCK_TIMEOUT_SECONDS = 120` (bounded wait) and `AUTH_LOCK_POLL_SECONDS = 0.5`
  (the delay between tries). The loop is `for _ in range(ceil(timeout / poll))`, so it has a fixed number of polls.
- Exit: when the wait is over, the run fails with a named error (`CodexAuthLockError`) that gives the lock path. The
  runner does not break a stale lock by itself: a crash can leave `auth.lock` behind, and the operator removes it.
- Shortcut with a known limit (to be commented in code): copy-in and copy-out with a lock do not stop two runs from
  refreshing the same refresh token at once. If the open checks confirm refresh-token rotation (HYPOTHESIS, below),
  hold the lock for the whole run instead.

**Compose volume (documented only, not added)**

The operator adds a named volume `ahawr-codex-auth` to the `ahawr-runner` service next to
`ahawr-provider-secrets:/secrets` (`docker-compose.yml:198`), mounted at `/codex-auth`, and declares it under the top-level
`volumes:` next to `ahawr-provider-secrets:` (`docker-compose.yml:463`). It sets `CLAUDE_RUNNER_CODEX_AUTH_DIR=/codex-auth`.
The operator creates the login there with the open checks above; no task runs `codex login`.

**Tests to add with the code (not written here)**

- `tests/test_config.py`: `CODEX_AUTH` values and the rejected `CLAUDE_RUNNER_CODEX_AUTH_DIR` locations.
- `tests/test_codex_config.py`: no `env_key` when `codex_auth` is `chatgpt`; the output is unchanged for `api_key`.
- `tests/test_codex_auth.py`: the lock times out with `CodexAuthLockError` after the bounded wait; copy-in and copy-out.

**Risks**

- Refresh-token rotation and the headless refresh are HYPOTHESIS (see above). The ChatGPT base URL in
  `config.toml` is not known from any in-scope source.

### Subscription login: status 2026-11-05

Implemented (flag off by default): `CODEX_AUTH=chatgpt` and `CODEX_LOGIN_HOME` on the provider block, `codex_auth.py`
(copy `auth.json` into the run's Codex home, write a refreshed one back; last run wins), no `model_provider` table in
`config.toml`, failure class `codex_not_logged_in` when there is no usable login. The login itself is done once by the
operator with `codex login --device-auth` and `CODEX_HOME=/home/node/.claude/codex-login`; the steps are in
`ahawr-runner/README.md`, "Signing in: Anthropic and Codex". Verified: login status, the Codex CLI in the image
(`AHAWR_INSTALL_CODEX_CLI=true`). Not yet run: a full run on the subscription (it spends the subscription's limit).
The Codex client was checked end to end on the local model instead: provider `CODEXLOCAL`
(`ENGINE=codex`, `CODEX_AUTH=api_key`, `CODEX_BASE_URL=http://host.docker.internal:8080/v1`, key variable `STRATA_KEY`),
Strata `/v1/responses`, role worker: the run completes, events and tool results are recorded, `.env` is denied.
