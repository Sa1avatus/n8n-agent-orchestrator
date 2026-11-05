# Run event format (schema_version 1)

The per-run activity log: what the agent was asked, thought, said, ran and returned. Written by the
runner, read by the dashboard, the QA view, the statistics and the export.

Paths below are relative to the repository root; `events.py:335` means
`ahawr-runner/src/ahawr_runner/events.py:335`.

**Covered engines:** the Claude Code CLI stream and the Hermes gateway. The Codex engine is out of
scope here (`codex_events.py` documents its own mapping in `docs/codex-executor.md`).

## Where the log lives

| fact | value | code |
|---|---|---|
| file | `<data_dir>/events/<run_id>.jsonl` | `runs.py:223`, `config.py:647,796` (`data_dir` = `CLAUDE_RUNNER_DATA_DIR`, default `/data`) |
| line shape | one JSON object per line, UTF-8, no wrapping | `events.py:338` |
| run id in the file name | `[A-Za-z0-9_-]{1,80}` | `events.py:304-306` |
| opened / closed | opened before the first event, closed after the `end` event | `runs.py:428`, `runs.py:823` |
| retention | files older than `event_retention_days` (default 14) are deleted on start-up | `events.py:308-318`, `config.py:714,850` |
| entry point | `GET /v1/runs/{run_id}/events` returns `events` + `next` + `live`; the dashboard mirrors it at `/api/runs/{run_id}/events` | `run_views.py:110-122`, `dashboard.py:508-518,567-569` |

Token deltas (`--include-partial-messages`) are **not** stored: they only feed the live block
(`events.py:1-8`, `events.py:157-189`).

Every text field written by `entries_from` is clipped to `MAX_TEXT = 20_000` characters with the
suffix `\n… [N more characters]` (`events.py:20,26-29`); a tool `input` is clipped to
`MAX_INPUT = 8_000` characters, keeping the keys and trimming long string values to `MAX_INPUT // 2`
(`events.py:23,51-57`).

## Version marker

`schema_version` is an integer field on each event object. The shared writer emits it only when the
`event_schema_version` feature in `features_json` is on (`events.py:343-349`, the flag read in `api.py` at
app start, off by default). With the feature off every event on disk is **version 0** — the format
described below, as it stands, and the written bytes are exactly as before the flag existed.

| version | meaning |
|---|---|
| absent | version 0: today's events. Readers accept entries without the marker; they must not reject them. |
| 1 | this document: the format described here, marked explicitly |

Compatibility rules under these versions:

| change | version bump | reader behaviour |
|---|---|---|
| a new optional field on a kind | none | read it only if present; ignore unknown fields |
| an unknown field in an entry | none | ignored, not an error |
| a field renamed | yes (`schema_version` 2) | the old name stops appearing; readers must handle both, or migrate |
| a field removed | yes | readers that still look for it see nothing, not an error |
| a type change (string → number, object → array) | yes | readers must check the type before use |
| a change of meaning or unit (ms → seconds, "steps" → "requests") | yes | the field name is the same, the value is not |

A writer that emits `schema_version` writes it as the first field, so a reader can check the version
before reading any other field.

## Envelope — every event

Written by `EventLog.add` (`events.py:330-339`); `kind` is supplied by each writer.

| field | type | required | default when absent | unit | writer | readers |
|---|---|---|---|---|---|---|
| `schema_version` | int | optional | absent = 0 | the version of this format | `events.py:348` only with `event_schema_version` on | `contract_schema.py:214-221` |
| `seq` | int | yes | — | ordinal in the run, 1-based | `events.py:335` | `dashboard.html:723,739,876,1143`; `next` cursor `run_views.py:120` |
| `t` | float | yes | — | epoch seconds, UTC, 3 decimals | `events.py:335` | `dashboard.html:964-965,979,1070-1071`; `board_stats.py:78`; `local_pricing.py:112,122` |
| `kind` | string | yes | — | name of the event kind | every writer, e.g. `events.py:71` | `dashboard.html:993-1007,1018-1035`; `run_views.py:34-39`; `export.py:141` |
| `agent` | string | optional | absent for the main thread | the CLI's `parent_tool_use_id` (subagent activity) | `events.py:63,151-153` | `dashboard.html:875,1074,1120,1146`; `export.py:162,165`; `handoff.py:52` |
| `step` | int | optional | absent | the number of the model request of the main thread | `events.py:349-352` | `dashboard.html:875,1120`; `export.py:162` |

`seq` counts entries of the run, not the file's lines: `read(run_id, after)` slices the list by
index, not by `seq` (`events.py:368-381`). Subagent entries carry `agent` and are shown as sub-lines;
they never carry `step` (subagent requests are not the run's own steps, `events.py:236-237`).

## Kinds written by the Claude Code engine

All entries below come from `entries_from` (`events.py:60-154`) unless the writer column names
another module.

### `prompt` — the task sent to the run

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `text` | string | yes | — | characters | `runs.py:429`; QA runs `qa_run_record.py:113` | `dashboard.html:1018`; `export.py:142`; `handoff.py:55-56` (capped `PROMPT_CHARS = 400`, `handoff.py:21`) |
| `resume` | bool | required in the worker path, absent in the QA path | — | — | `runs.py:429` | no reader yet |

### `init` — the session the run started

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `model` | string | yes | `""` | model id | `events.py:105-112` | `dashboard.html:1020` |
| `cwd` | string | yes | `""` | absolute path | `events.py:107` | `dashboard.html:1020` |
| `permission_mode` | string | yes | `""` | CLI mode name | `events.py:108` | `dashboard.html:1020` |
| `tools` | array of strings | yes | `[]` | tool names | `events.py:109` | `dashboard.html:1020` (count) |
| `version` | string | yes | `""` | Claude Code version | `events.py:110` | `dashboard.html:1020` |

### `user`, `thinking`, `text` — content blocks

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `text` | string | yes | `""` | characters, clipped at 20 000 | `user`: `events.py:99,101`; `thinking`: `events.py:71,73`; `text`: `events.py:75` | `dashboard.html:1019-1024`; `export.py:166-169`; `handoff.py:57-58` |

`redacted_thinking` blocks become a `thinking` entry whose `text` is the literal
`"[redacted by the provider]"` (`events.py:72-73`).

### `tool_use` — a call the agent made

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `id` | string | yes | `""` | correlation id with the matching `tool_result` | `events.py:79-84` | `dashboard.html:726,864,979,1000,1025`; `export.py:138,171`; `handoff.py:59-63` |
| `name` | string | yes | `""` | tool name | `events.py:81` | `dashboard.html:1025`; `run_views.py:39-41` (edits counted for `EDIT_TOOLS`); `export.py:171`; `handoff.py:60` |
| `input` | object | yes | `null` when the block has no input | tool arguments, clipped to 8 000 chars | `events.py:82`, `events.py:51-57` | `dashboard.html:1025,1147-1156`; `run_views.py:40` (`input.file_path`); `export.py:171`; `handoff.py:61` |

### `tool_result` — the outcome of a call

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `id` | string | yes | `""` | matches the `tool_use` `id` | `events.py:93` | `dashboard.html:865,1001`; `export.py:137,189`; `handoff.py:50,63-65` |
| `is_error` | bool | yes | `false` when the CLI block omits it | — | `events.py:94` | `dashboard.html:1001,1153`; `export.py:180`; `handoff.py:64` |
| `text` | string | yes | `""` | characters, clipped at 20 000 | `events.py:95` | `dashboard.html:1028`; `export.py:189`; `handoff.py:65` (capped `TOOL_RESULT_CHARS = 400`, `handoff.py:19`) |

### `step` — one model request of the main thread

Written by `StepTracker` (`events.py:201-286`): one entry per request, closed by the next
`message_start` or by the `result`.

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `step` | int | yes | — | request number, 1-based | `events.py:249,278` | `dashboard.html:1059`; `export.py:136,162` |
| `model` | string | yes | `""` | model id | `events.py:250,279` | `dashboard.html:970`; `export.py:86` |
| `started` | float | yes | — | epoch seconds, 3 decimals | `events.py:251,280` | `dashboard.html:863,968-973,1059,1134`; `board_stats.py:94`; `local_pricing.py:122` |
| `input_tokens` | int | yes | `0` | tokens | `events.py:192-198` | `mission_stats.py:56,89`; `local_pricing.py:129` (`FIELDS`, `local_pricing.py:28`) |
| `cache_read_tokens` | int | yes | `0` | tokens | `events.py:195` | `mission_stats.py:57,90`; `export.py:96-98` |
| `cache_write_tokens` | int | yes | `0` | tokens | `events.py:196` | `mission_stats.py:57`; `local_pricing.py:129` |
| `output_tokens` | int | yes | `0` | tokens | `events.py:197` | `mission_stats.py:57,94`; `dashboard.html:897,970,1055`; `export.py:102` |
| `duration_ms` | int | optional | absent until the step closes | milliseconds | `events.py:218` | `dashboard.html:863,969-970,1059,1127,1135`; `export.py:88-89` |
| `ttft_ms` | int | optional | absent when no token delta was seen | milliseconds to the first token | `events.py:220` | `dashboard.html:969-970`; `export.py:90-91` |
| `generation_ms` | int | optional | absent when the reply was not streamed token by token | milliseconds | `events.py:222` | `mission_stats.py:92-95`; `export.py:92-93` |
| `tokens_per_s` | float | optional | absent when speed is not measurable | tokens per second, 1 decimal | `events.py:232` | `dashboard.html:897,970,1055,1138`; `export.py:102-103` |
| `stream_buffered` | bool | optional | absent | set when the measured speed exceeded `MAX_PLAUSIBLE_TPS = 600` tok/s (`events.py:22,225-229`); `generation_ms` is then removed | `events.py:229` | no reader yet; the effect is seen as the missing `generation_ms` in `mission_stats.py:92` |

A step without token deltas (no `--include-partial-messages`) gets one entry per API message, tokens
only (`events.py:267-283`).

### `retry` — an API call retried by the CLI

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `attempt` | int or null | optional | `null` | retry number | `events.py:117` | `dashboard.html:965,1029` |
| `max_retries` | int or null | optional | `null` | retries | `events.py:118` | `dashboard.html:1029` |
| `status` | int or null | optional | `null` | HTTP status | `events.py:119` | `dashboard.html:965,1029` |
| `error` | string or null | optional | `null` | error name from the CLI | `events.py:120` | `dashboard.html:965,1029`; `export.py:198-203` |
| `delay_ms` | int or null | optional | `null` | milliseconds | `events.py:121` | `dashboard.html:1029` |

### `compact` — the context was compacted

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `trigger` | string or null | optional | `null` | CLI trigger (`manual`, `auto`) | `events.py:129` | `dashboard.html:1031`; `export.py:204-208` |
| `pre_tokens` | int or null | optional | `null` | tokens | `events.py:130` | `dashboard.html:1031`; `handoff.py:68` |
| `post_tokens` | int or null | optional | `null` | tokens | `events.py:131` | `dashboard.html:1031`; `handoff.py:68` |
| `duration_ms` | int or null | optional | absent in the recorded logs (the CLI reports it only in newer versions) | milliseconds | `events.py:133` | `dashboard.html:1031`; `export.py:207-208` |

Read as a count too: `run_views.py:35` (compactions of a run), `mission_stats.py:86`.

### `result` — the final result of the run

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `subtype` | string | yes | `null` when absent | CLI subtype (`success`) | `events.py:140` | `dashboard.html:1032` |
| `is_error` | bool | yes | `false` | — | `events.py:141` | `dashboard.html:1004,1032`; `export.py:227` |
| `text` | string | yes | `""` | characters, clipped at 20 000 | `events.py:142` | `dashboard.html:1032`; `export.py:227-228` |
| `num_turns` | int or null | optional | `null` | turns | `events.py:143` | `dashboard.html:1032,1167`; `export.py:213-214` |
| `cost_usd` | float or null | optional | `null` | USD | `events.py:144` | `dashboard.html:1032` |
| `duration_ms` | int or null | optional | `null` | milliseconds | `events.py:145` | `dashboard.html:910,1032`; `export.py:215-216` |
| `duration_api_ms` | int or null | optional | `null` | milliseconds | `events.py:146` | `dashboard.html:911,1169` |
| `usage` | object or null | optional | `null` | the CLI usage object as-is: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens` | `events.py:147` | `export.py:219-224` |
| `model_usage` | object or null | optional | `null` | keyed by model name | `events.py:148` | `dashboard.html:1172` |

### `end` — the run finished

| field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|
| `status` | string | yes | — | `completed`, `failed`, `cancelled`, `interrupted` (`runs.py:760`) | `runs.py:816-821`; QA path `qa_run_record.py:143` | `dashboard.html:1005,1034`; `export.py:229` |
| `error_code` | string | yes | `""` | runner code | `runs.py:818` | `dashboard.html:1034`; `export.py:229` |
| `error` | string | yes | `""` | message | `runs.py:819` | `dashboard.html:1034` |
| `stderr_tail` | string | optional | `""` | characters, last 2 000 of stderr | `runs.py:820` | `export.py:234-235` (fenced as a stderr block when `status` is not `completed`); `dashboard.html:1173` |

### Runner bookkeeping kinds

Written by the runner about itself, not by the engine stream.

| kind | field | type | required | default | unit | writer | readers |
|---|---|---|---|---|---|---|---|
| `git_snapshot` | `ref` | string | yes | — | git ref `refs/ahawr/snapshots/<run_id>` (`git_snapshot.py:22,47`) | `runs.py:478` (only when `worker_git_snapshot` is on, `config.py:689`) | no reader yet |
| `thinking_state` | `thinking` | string | yes | — | `"on"` / `"off"` | `runs.py:484`, `runs.py:529-534` | `board_stats.py:75-78`; `local_pricing.py:109-111` |
| | `variant` | string | optional | absent (local runs have no variant) | think variant name | `runs.py:531` | no reader yet |
| | `retry` | bool | optional | absent | — | `runs.py:532` | no reader yet |
| `thinking_switch` | `thinking` | string | yes | — | `"on"` / `"off"` | `think_switch.py:310,433,452,513-520` | `board_stats.py:75-78`; `local_pricing.py:111-112` |
| | `reason` | string | yes | — | human reason | `think_switch.py:310,433,452` | no reader yet |
| | `session_seconds` | float | optional | absent | seconds | `think_switch.py:515` | no reader yet |
| | `limit_seconds` | float | optional | absent | seconds | `think_switch.py:516` | no reader yet |
| | `steps` | int | optional | absent | steps | `think_switch.py:518` | no reader yet |
| | `limit_steps` | int | optional | absent | steps | `think_switch.py:518` | no reader yet |
| `context_window` | `source` | string | yes | — | `"probe"` / `"static"` | `runs.py:580` | no reader yet |
| | `probed` | int or null | optional | `null` | tokens | `runs.py:581` | no reader yet |
| | `window` | int or null | optional | `null` | tokens | `runs.py:582` | no reader yet |
| | `compact_window` | int or null | optional | `null` | tokens | `runs.py:583-584` | no reader yet |
| | `compact_pct` | string or null | optional | `null` | percent | `runs.py:585` | no reader yet |
| `qa_verdict` | `role` | string | yes | — | role name (`"qa"`) | `runs.py:807`; `qa_run_record.py:141` | no reader yet (tests: `test_runs.py:642`) |
| | `status` | string | yes | — | `completed` / `failed` | `runs.py:808`; `qa_run_record.py:141` | — |
| | `summary` | string | yes | — | characters, capped `QA_SUMMARY_CHARS` | `runs.py:809`; `qa_run_record.py:141` | — |
| | `cost_usd` | float | optional | absent on the QA-request path | USD | `runs.py:810` | — |
| `qa_scenario` | `scenario` | string | optional | `""` | scenario name, capped `SCENARIO_MAX_CHARS` | `qa_run_record.py:61-62` | `dashboard.html:1006,1033` |
| | `layer` | string | optional | `""` | QA layer | `qa_run_record.py:63` | `dashboard.html:1033` |
| | `verdict` | string | yes | `"unknown"` | `pass` / `fail` / `skipped` | `qa_run_record.py:64` | `dashboard.html:1006,1033` |
| | `reason` | string | optional | `""` | characters, capped `REASON_MAX_CHARS` | `qa_run_record.py:65` | `dashboard.html:1033` |
| | `minutes` | float | optional | absent | minutes | `qa_run_record.py:67-68` | no reader yet |
| `mem_guard` | `pid` | int | yes | — | process id | `mem_guard.py:190` | the ops parsers read the same JSON line from the ops snapshot text, `board_ops_parsers.py:389-430` |
| | `process` | string | yes | — | process name | `mem_guard.py:191` | same |
| | `memory_mb` | int | yes | — | anonymous MB | `mem_guard.py:192` | same |
| | `reason` | string | yes | — | human reason | `mem_guard.py:193` | same |
| | `command` | string | optional | `""` | characters, first 300 of the command line | `mem_guard.py:194` | same |

`mem_guard` entries are added to the event log of every run that is still running
(`mem_guard.py:186-199`); they are one of the few kinds with no dashboard badge — the badge switch
falls to the default branch (`dashboard.html:1007`).

## Kinds written by the Hermes engine

Hermes runs get the same envelope and the same `prompt` and `end` events (`runs.py:429`,
`runs.py:816-821`); the engine entries come from `hermes_events.result_entries`
(`hermes_events.py:104-136`, added in `runs.py:710-712`). A Hermes run has **no `step` entries** and
no `agent` entries: there is no token delta stream to track.

| kind | route (`hermes_events.py:24-34`) | fields | writer | readers |
|---|---|---|---|---|
| `text` | `completed` | `text` (string, required, characters) | `hermes_events.py:108,136` | `dashboard.html:1023`; `export.py:169` |
| `retry` | `transient_error` | `attempt` (int, required, retry number), `max_retries` (always `null`), `status` (int or null, HTTP code), `error` (string, required), `delay_ms` (always `null`) | `hermes_events.py:110-119` | `dashboard.html:1029` |
| `compact` | `restart_session`, `start_new` | `trigger` = `"handoff"`, `pre_tokens` / `post_tokens` / `duration_ms` all `null` | `hermes_events.py:121-129` | `dashboard.html:1031` (shows empty token counts) |
| `notice` | `failed`, `cancelled`, `canceled`, `run_not_found`, `timeout`, any unmapped route | `text` (string, required; the error text, or `Hermes run <id>: poll <n>` when the payload carries none, `hermes_events.py:133-135`) | `hermes_events.py:22,136` | no case in `badgeFor`, so the default label (`dashboard.html:1007`) and the default summary (`dashboard.html:1035`) |

Routes in `RUNING_ROUTES` (`running`, `started`, `queued`, `waiting_for_approval`) produce no entry
(`hermes_events.py:20,130-132`).

## Fields a reader sees that are not in the log

Two read-side shapes exist so the dashboard can show a run that has no (or no complete) event log.

**The live block** (`events.py:157-189`): the block being generated right now, returned next to the
entries by `read` (`events.py:375-376`) and by `run_views.py:119-121`. Fields: `kind` (`"text"`,
`"thinking"` or `"tool_use"`, `events.py:176`), `name`, `text` (characters, newest 20 000 kept),
`agent`, `started` (epoch seconds). Written at `events.py:175-181`, read at `dashboard.html:1079-1080`.
It is never written to the jsonl.

**Hermes session messages** (`sources.py:174-242`): entries built from the gateway's message API,
returned by the same events route (`dashboard.py:518`). They reuse the kinds above and add:

| field | type | unit | writer | readers |
|---|---|---|---|---|
| `t` | float or absent | epoch seconds | `sources.py:185,187` | `dashboard.html:964-965` |
| `step` | int | request number, 1-based | `sources.py:199,201,213,218` | `dashboard.html:875,1059` |
| `finish_reason` | string | reason name | `sources.py:221` | `dashboard.html:1056,1133` |
| `approximate` | bool | always `true` for a Hermes step: the duration is measured between messages, not from token deltas | `sources.py:222` | `dashboard.html:1059,1127,1135,1139`; `export.py:89` |
| `started` | float | epoch seconds | `sources.py:225,231` | `dashboard.html:863,968-973,1059,1134` |
| `duration_ms` | int | milliseconds | `sources.py:226` | `dashboard.html:863,969-970,1059,1127,1135` |
| `tokens_per_s` | float | tokens per second | `sources.py:229` | `dashboard.html:1055,1138` |
| `is_error` | bool | tool call failed | `sources.py:163-172` | `dashboard.html:1001,1153` |
| `preview` | string | characters, first 2 000 of the readable part | `sources.py:171` | `dashboard.html:1027,1156,1189-1190` |
| `name` (on `tool_result`) | string | tool name | `sources.py:238` | `dashboard.html:1074` context |

## Examples

### Claude Code engine

From the staged log `docs/cold-prefill/partB-runner-events/run_001876b6ff33467b8c7afaa0bfd3f1ff.jsonl`
(paths, prompt text and tool output truncated here; the run's `end` is line 95):

```json
{"seq": 1, "t": 1791594767.068, "kind": "prompt", "text": "You are a local coding Worker. …"}
{"seq": 2, "t": 1791594767.077, "kind": "context_window", "source": "probe", "probed": 131072, "window": 131072, "compact_window": 131072, "compact_pct": "67.66"}
{"seq": 3, "t": 1791594768.012, "kind": "init", "model": "qwen3.8-27b-gsq-rco-iq2-s-mtp", "cwd": "<work dir>", "permission_mode": "bypassPermissions", "tools": ["Bash", "Edit", "Glob", "Grep", "Read", "Write"], "version": "2.1.286"}
{"seq": 4, "t": 1791594816.132, "kind": "text", "text": "I'll start by exploring the scope: the results JSONs, PLAN.md, the patches, and research-notes.md.", "step": 1}
{"seq": 5, "t": 1791594820.581, "kind": "tool_use", "id": "iXgcvtEdH6w0OZGo11lG5HVEh3xVEA7f", "name": "Bash", "input": {"command": "ls <work dir>/results/ | head -60; …"}}
{"seq": 6, "t": 1791594820.595, "kind": "step", "step": 1, "model": "qwen3.8-27b-gsq-rco-iq2-s-mtp", "started": 1791594814.981, "input_tokens": 15303, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 164, "duration_ms": 5614, "ttft_ms": 1145, "generation_ms": 4470, "tokens_per_s": 36.7}
{"seq": 7, "t": 1791594820.815, "kind": "tool_result", "id": "iXgcvtEdH6w0OZGo11lG5HVEh3xVEA7f", "is_error": false, "text": "a-phase0.json\na-phase0.log\n…"}
{"seq": 95, "t": 1791602015.859, "kind": "end", "status": "failed", "error_code": "timeout", "error": "Claude Code run timed out after 7200 s.", "stderr_tail": "…"}
```

Two other recorded kinds, from other logs in the same directory:

```json
{"kind": "retry", "attempt": 1, "max_retries": 10, "status": 500, "error": "server_error", "delay_ms": 506}   // run_020b26ce8a3141218265994e0415fae8.jsonl
{"kind": "compact", "trigger": "manual", "pre_tokens": 56401, "post_tokens": 6103}                           // cmp_010e28b16e6249df9e21eed7325a901d.jsonl
```

Kind census of those logs (1 341 files, `grep -o '"kind": "[a-z_]*"' | sort | uniq -c`): tool_use
26 164, tool_result 26 161, step 22 565, text 8 137, thinking 3 712, init 1 376, result 1 359, prompt
1 338, end 1 322, context_window 568, user 454, compact 363, retry 136, thinking_switch 13. No
`git_snapshot`, `mem_guard`, `qa_verdict`, `qa_scenario` or `notice` lines appear in that sample.

The conformance test reads a clipped copy of a live log of the same kind:
`tests/fixtures/events_claude_code.jsonl` (38 lines from
`/data/events/run_b9cc0264d28d41348c9e7714818ffa9b.jsonl`, with `cwd` and the work paths
replaced by `/workspace` and long prompt/thinking/tool text clipped); every line of it passes
`validate_run_event` (`contract_schema.py:261`).

### Hermes engine

No recorded Hermes event log exists on the host (the staged logs above contain no `notice` entry; the
staged `docs/cold-prefill/partB-runner.sqlite` has no run of a Hermes engine). The entries below are
the shapes the mapping produces, checked against `tests/test_hermes_events.py:26,37,53`:

**Constructed illustration:** built from `hermes_events.py:104-136` and `tests/test_hermes_events.py`,
not a recorded log. The same route produced the conformance fixture
`tests/fixtures/events_hermes.jsonl` (written by `EventLog.add`, `events.py:349`).

```json
{"seq": 1, "t": 1791602016.101, "kind": "prompt", "text": "…"}
{"seq": 2, "t": 1791602021.418, "kind": "retry", "attempt": 2, "max_retries": null, "status": null, "error": "upstream error", "delay_ms": null}
{"seq": 3, "t": 1791602051.776, "kind": "compact", "trigger": "handoff", "pre_tokens": null, "post_tokens": null, "duration_ms": null}
{"seq": 4, "t": 1791602111.034, "kind": "text", "text": "the answer"}
{"seq": 5, "t": 1791602111.205, "kind": "end", "status": "completed", "error_code": "", "error": ""}
```

## Changing a writer

1. Name the kind and every field you add; add it to this document (fields the readers do not read
   yet are listed as "no reader yet" above - that is not a licence to drop them).
2. Clip text to `MAX_TEXT` / `MAX_INPUT` (`events.py:20-23`).
3. If the change renames, removes, retypes or re-means a field, bump `schema_version` and update the
   readers listed above.
4. Check the readers before and after: `run_views.py:30-50`, `dashboard.html:993-1035`,
   `board_stats.py:69-95`, `mission_stats.py:45-95`, `local_pricing.py:95-129`, `export.py:136-229`,
   `handoff.py:43-72`, `board_ops_parsers.py:389-430`.
