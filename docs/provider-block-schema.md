# Provider block schema (schema_version 1)

One provider block: the environment variables that describe one model backend, chosen per run by the
request's `provider` (the `*_provider` of the role, as with Hermes). **Written** by the operator in
`.env` / `docker-compose.yml` and parsed by the runner at start-up; **read** by the Claude Code CLI,
the Codex engine, the Hermes engine, the context probe and the compaction logic.

Paths are relative to the repository root; `config.py:480` means
`ahawr-runner/src/ahawr_runner/config.py:480`.

Scope: the env-backed block only. `ahawr-runner/docs/providers.md` (the Providers DB) and
`docs/codex-executor.md` / `docs/hermes-engine.md` are the engine-side documents this schema feeds.

## Shape of a block

| fact | value | code |
|---|---|---|
| key pattern | `CLAUDE_RUNNER_PROVIDER_<NAME>__<KEY>=value` (double underscore) | `config.py:162`, `config.py:457-459` |
| block name → provider key | `custom:llama-local` → `CUSTOM_LLAMA_LOCAL` | `config.py:308-310` |
| every value | a string; the parser converts it (int, float, bool, URL) | `config.py:451-523` |
| keys in `PROVIDER_RUNNER_KEYS` | configure the runner, never reach a CLI process | `config.py:177-197`, `config.py:480` |
| every other key | lands in `env` as-is and reaches the CLI process | `config.py:480`, `claude_cli.py:260` |
| no block for a name | the run uses the container env, no `ProviderProfile` | `config.py:899-901` |
| Providers DB (`CLAUDE_RUNNER_PROVIDERS_DB=1`) | a DB row replaces the env block for that name | `config.py:865`, `provider_registry.py:220-233` |
| a bad value | `ValueError` at settings load, the runner does not start | `config.py:338-419`, `config.py:427-448` |

## Version marker

`schema_version` is a document-level marker, not a stored field: no provider-block writer emits it
(the event writer stamps only run events, `ahawr-runner/src/ahawr_runner/events.py:348`), so every
block on disk is **version 1** — the shape described below. The rules are the same as in
`docs/run-event-format.md:42-51`.

| change | version bump | reader behaviour |
|---|---|---|
| a new optional key in a block | none | read it only if present; ignore unknown keys |
| an unknown key in a block | none | lands in `env` (`config.py:480`), ignored by the runner, not an error |
| a key renamed | yes (schema_version 2) | the old key stops appearing; readers must handle both, or migrate |
| a key removed | yes | readers that still look for it see nothing, not an error |
| a type change (string → number, list → object) | yes | readers must check the type before use |
| a change of meaning or unit (ms → seconds, "polls" → "requests") | yes | the key is the same, the value is not |

## Fields

`required` here means "the block must name it": a missing key takes the default, not an error.
A default of "derived" means the runner computes it from the other keys when the block does not name
it.

### Engine selection

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `ENGINE` | `engine` | string: `claude` / `codex` / `hermes` | optional | `claude` (`config.py:199`) | value, no unit | `config.py:493`, `_engine` `config.py:359-366` | `runs.py:345,349` (dispatch), `runs.py:489,494`, `runs.py:1017` |

`hermes` is accepted only with the flag `CLAUDE_RUNNER_HERMES_ENGINE_ENABLED` on
(`config.py:361`: `accepted = (*ENGINES, "hermes") if hermes_engine_enabled else ENGINES`; the flag
defaults off, `config.py:794`), and `ENGINES` itself is `("claude", "codex")` (`config.py:198`). An
unknown `ENGINE` value is a configuration error (`ValueError`, `config.py:362-365`).

### Claude Code limits and compaction

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `TOOLS` | `tools` | string, trimmed (`.strip()`) | optional | `""`; a non-empty provider value is used only when the role names no tools of its own | a comma-separated list of Claude Code tool names, no unit | `config.py:481`, `ProviderProfile.tools` `config.py:263` | `config.py:926-928` (`tools_for`: role tools, else provider tools, else the global `CLAUDE_RUNNER_TOOLS` `config.py:710`), `claude_cli.py:167` then `claude_cli.py:170` (`--tools`) |
| `COMPACT_MIN_TOKENS` | `compact_min_tokens` | int | optional | `None`; the global `CLAUDE_RUNNER_COMPACT_MIN_TOKENS` = 120000 (`config.py:813`) | tokens | `config.py:482`, raw `config.py:464` | `runs.py:988`, `runs.py:1024-1025` |
| `COMPACT_MIN_PCT` | `compact_min_pct` | float | optional | `None`; derived from `COMPACT_MIN_TOKENS` scaled to the probed window (`context_probe.py:114-116`) | percent of the window, valid 0-100 | `config.py:488`, `_pct` `config.py:338-345` | `context_probe.py:114-116` |
| `COMPACT_PCT` | `compact_pct` | float | optional | `None`; derived from `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` or the static trigger (`context_probe.py:90-99`) | percent of the window, valid 0-100 | `config.py:487` | `context_probe.py:94-95`, `context_probe.py:103-105` |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | `autocompact_threshold` | float | optional | `None` = the provider's built-in default | percent of the 65536 window (`config.py:228`) | `config.py:483`, `_autocompact_threshold` `config.py:318-335` | `context_probe.py:96,99`; validated `config.py:427-448` |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | `autocompact_threshold` | int | optional | `None` when neither key is set | tokens; trigger = window − min(max_output, 20000) − 13000 (`config.py:333-334`) | `config.py:483` | `context_probe.py:99`; validation `config.py:434-448` |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | `max_output_tokens` | int | optional | `None` = 8192 (`config.py:230`) | tokens per response | `config.py:484`, `_max_output_tokens` `config.py:422-424` | `context_probe.py:86`, `config.py:333`; reaches the CLI env (`claude_cli.py:260`) |
| `CONTEXT_PROBE_URL` | `context_probe_url` | string URL | optional | `""` = static settings only | n/a (a URL) | `config.py:485` | `runs.py:568-571`, `runs.py:1027-1030`, `context_probe.py:65-77` |
| `CONTEXT_PROBE_KEY` | `context_probe_key` | string (a bearer token value — a secret, never printed) | optional | `""` | n/a | `config.py:486` | `context_probe.py:69-70`; kept out of the CLI env (`config.py:164-165`, `config.py:480`) |

The compaction keys are checked against the window at load: the trigger must fit in 65536
(`config.py:228`) and leave the 20000-token tool reserve (`config.py:229`) — otherwise the runner
refuses to start (`config.py:434-448`).

`TOOLS` is the reduced built-in tool set of the run, handed to Claude Code as `--tools`
(`claude_cli.py:168-170`): the comment there says the smaller set shrinks the system prompt (~15k to
~4k tokens), which matters for local models with a small window. `runs.py` reads nothing from it
(grep `tools` in `runs.py`: no matches); the Providers DB stores the same string in the `tools`
column (`ahawr-runner/docs/board-schema.sql:137`).

### Hermes engine keys

Engine keys name the variables the engine reads (names, never values) and bound its polling loop.

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `HERMES_BASE_URL_VAR` | `hermes_base_url_var` | string, an env var name | optional | `HERMES_API_URL` (`config.py:206`) | n/a | `config.py:504-508`, `_env_var_name` `config.py:376-383` | `hermes_run.py:72` |
| `HERMES_API_KEY_VAR` | `hermes_api_key_var` | string, an env var name | optional | `API_SERVER_KEY` (`config.py:207`) | n/a | `config.py:509-513` | `hermes_run.py:50` |
| `HERMES_START_TIMEOUT_MS` | `hermes_start_timeout_ms` | int | optional | 10000 (`config.py:208`) | milliseconds | `config.py:466-469`, `config.py:513`, `_positive_ms` `config.py:386-394` | `hermes_run.py:79` |
| `HERMES_POLL_INTERVAL_MS` | `hermes_poll_interval_ms` | int | optional | 3000 (`config.py:209`) | milliseconds | `config.py:470-473`, `config.py:514` | `hermes_run.py:166` |
| `HERMES_RUN_TIMEOUT_MS` | `hermes_run_timeout_ms` | int | optional | 3600000 (`config.py:210`) | milliseconds | `config.py:474-477`, `config.py:515` | `hermes_run.py:154` |
| `HERMES_MAX_POLLS` | `hermes_max_polls` | int | optional | derived: `run_timeout_ms // poll_interval_ms` (`config.py:299`, `_hermes_max_polls` `config.py:397-406`) | polls | `config.py:516-518` | `hermes_run.py:157` (the loop bound) |

Zero or a non-integer timeout/poll is a `ValueError` (`config.py:388-394`, `config.py:399-404`).
The bound, the delay and the exit are one loop: `hermes_run.py:154-166`.

### Codex engine keys

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `CODEX_BASE_URL` | `codex_base_url` | string, http(s) URL | optional | `https://api.openai.com/v1` (`config.py:200`) | n/a | `config.py:494`, `_codex_base_url` `config.py:369-373` | `codex_config.py:93` |
| `CODEX_API_KEY_VAR` | `codex_api_key_var` | string, an env var name | optional | `OPENAI_API_KEY` (`config.py:201`) | n/a | `config.py:495-497` | `codex_config.py:98-99`, `codex_cli.py:75-77` |
| `CODEX_STREAM_IDLE_TIMEOUT_MS` | `codex_stream_idle_timeout_ms` | int | optional | 300000 (`config.py:202`) | milliseconds | `config.py:498-503` | `codex_config.py:96` |
| `<the key named by CODEX_API_KEY_VAR>` | lands in `env` like any other non-runner key | string (a secret) | optional for the codex engine | none | n/a | `config.py:480` | `codex_cli.py:76-77`; the Codex shell gets only this variable (`codex_cli.py:72-78`) |

A non-http(s) `CODEX_BASE_URL` is a `ValueError` (`config.py:371-373`).

### Pricing

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `PRICE_USD_PER_MILLION_TOKENS` | `price_usd_per_million_tokens` | four comma-separated non-negative numbers: `input,output,cache_read,cache_write` | optional | `None` = no price | USD per 1,000,000 tokens | `config.py:519-521`, `_price_usd_per_million_tokens` `config.py:409-419` | `codex_run.py:257-260` |

Only the Codex engine reads it today (grep `price_usd_per_million_tokens`: one reader); the Claude
engine prices runs from the local pricing table (`ahawr-runner/src/ahawr_runner/local_pricing.py`).

### Credentials and pass-through CLI settings

| key | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` | `env` entries | string (secrets for the first two) | optional | none (the container env) | n/a | `config.py:480`, `CREDENTIAL_VARS` `config.py:156-161` | `claude_cli.py:253-260` (`isolates_credentials`, `config.py:304-305`) |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | no field: pass-through into `env` | int | optional | unset = the CLI's own default | tokens | `config.py:480`; set from the probe (`context_probe.py:126-128`) | Claude Code |
| `BASH_MAX_OUTPUT_LENGTH`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | no field: pass-through into `env` | int | optional | unset; the runner adds 12000 and 16000 for the Worker on a local provider only, when the flag is on (`claude_cli.py:261-263`) | characters / tokens | `config.py:480` | Claude Code |
| `SEARCH_FIRST` | `search_first` | bool (`1/true/yes/on`, `0/false/no/off`) | optional | `None` = the global `CLAUDE_RUNNER_SEARCH_FIRST` (`config.py:851`) | n/a | `config.py:489`, `_opt_bool` `config.py:348-356` | `config.py:912-920`, `runs.py:546`, `claude_cli.py:197-201` |
| `RETRY_FRESH_SESSION` | `retry_fresh_session` | bool | optional | `None` = the global `CLAUDE_RUNNER_RETRY_FRESH_SESSION` (`config.py:857`) | n/a | `config.py:490-492` | `config.py:903-910`, `runs.py:344`, `runs.py:1020` |

`RETRY_FRESH_SESSION` is the only runner-wide key a provider block may override
(`ahawr-runner/README.md:138`); the other global keys stay global.

### Providers DB rows write the same fields

With `CLAUDE_RUNNER_PROVIDERS_DB=1` the dashboard writes rows of `board.providers`
(`ahawr-runner/docs/board-schema.sql:127-142`, validated in `board_providers_api.py:129-140`) that
become `ProviderProfile` objects (`provider_registry.py:56-77`): `tools`, `compact_pct`,
`compact_min_pct`, `max_output_tokens`, `search_first`, `retry_fresh_session`, `base_url` (→
`ANTHROPIC_BASE_URL`) and `context_tokens` (→ the three window keys, `context_probe.py:119-131`).
The table has no engine, Hermes or pricing column: those stay env-only.

## Not fields of a provider block

- **Error rules** live in the engine JSON (`error_rules`, `board_engine.py:24,274,334`;
`claude_cli.py:428,510`), not in a provider block.
- **Model** has no block key: the model comes from the role's `provider/model` string
(`ahawr-runner/docs/providers.md:4-31`); the block names a backend, not a model.
- **Global-only settings** (`CLAUDE_RUNNER_*` without a block) are not per-provider fields: the
  per-provider keys are exactly the 19 entries of `PROVIDER_RUNNER_KEYS` (`config.py:178-196`). The
  block names 22 keys in all: those 19 plus `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`,
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, which the parser converts
  into the fields `autocompact_threshold` and `max_output_tokens`; every other key lands in `env`.

## Example (made-up values)

Values below are invented for the schema; they are not the live configuration and contain no secret.

```dotenv
# A made-up provider named "testlab" running the Hermes engine.
CLAUDE_RUNNER_PROVIDER_TESTLAB__ENGINE=hermes
CLAUDE_RUNNER_PROVIDER_TESTLAB__HERMES_BASE_URL_VAR=TESTLAB_API_URL
CLAUDE_RUNNER_PROVIDER_TESTLAB__HERMES_API_KEY_VAR=TESTLAB_API_KEY
CLAUDE_RUNNER_PROVIDER_TESTLAB__HERMES_START_TIMEOUT_MS=8000
CLAUDE_RUNNER_PROVIDER_TESTLAB__HERMES_POLL_INTERVAL_MS=2000
CLAUDE_RUNNER_PROVIDER_TESTLAB__HERMES_RUN_TIMEOUT_MS=1800000
CLAUDE_RUNNER_PROVIDER_TESTLAB__COMPACT_PCT=72.5
CLAUDE_RUNNER_PROVIDER_TESTLAB__COMPACT_MIN_PCT=30
CLAUDE_RUNNER_PROVIDER_TESTLAB__COMPACT_MIN_TOKENS=40000
CLAUDE_RUNNER_PROVIDER_TESTLAB__CLAUDE_CODE_MAX_OUTPUT_TOKENS=4096
CLAUDE_RUNNER_PROVIDER_TESTLAB__CONTEXT_PROBE_URL=http://127.0.0.1:8088/v1
CLAUDE_RUNNER_PROVIDER_TESTLAB__CONTEXT_PROBE_KEY=<a placeholder token, never printed>
CLAUDE_RUNNER_PROVIDER_TESTLAB__SEARCH_FIRST=1
CLAUDE_RUNNER_PROVIDER_TESTLAB__RETRY_FRESH_SESSION=1
CLAUDE_RUNNER_PROVIDER_TESTLAB__TOOLS=Bash,Read
CLAUDE_RUNNER_PROVIDER_TESTLAB__PRICE_USD_PER_MILLION_TOKENS=1.5,5.0,0.5,2.0
```

The same pattern for a Codex block names the key variable, never its value:
`CLAUDE_RUNNER_PROVIDER_TESTLAB__CODEX_API_KEY_VAR=TESTLAB_OPENAI_KEY`, with the key itself held in a
separate variable.

## Naming of the keys

Per `docs/naming.md`: environment keys are `UPPER_SNAKE` with the component prefix
(`CLAUDE_RUNNER_`), units are in the name (`_MS`, `_TOKENS`, `_PCT`), and the engine keys name the
variable they read (`*_VAR`) rather than carrying a value. These names are the stored ones: a rename
across a boundary (an env key, a DB column) needs an alias, a changelog line and the operator's
approval (`docs/naming.md:96-98`).
