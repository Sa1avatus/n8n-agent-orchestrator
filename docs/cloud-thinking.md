# Cloud thinking: what the CLI can switch, and how the runner maps it

This document records the facts gathered in the T001–T005 notes
(`claude-runner/docs/cloud-thinking-notes.md`) and the per-process mapping the runner
uses for cloud (Anthropic) runs. It is a record of what is in the CLI and what the
runner decided to do about it; it proposes no schema change.

## 1. Installed CLI

- `claude --version` on the host prints `2.1.286 (Claude Code)` — the exact version
  string. Host install: `/usr/local/bin/claude` →
  `/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`; the
  `package.json` in that directory says `"version": "2.1.286"`.
- The CLI is a single ~240 MB native binary (`bin/claude.exe`); there is no `cli.js`
  to grep. All evidence below is quoted from that binary or from `claude --help`.

## 2. Flags and variables that switch thinking

Only the entries the notes marked as present in that CLI version are listed.
(`--effort` and `--model` are not thinking switches per se, but they decide the
session's thinking capability, so they are included.)

| name | present | evidence | effect |
|---|---|---|---|
| `--effort <level>` | yes | `claude --help`: "--effort <level>  Effort level for the current session (low, medium, high, xhigh, max)" | Sets the session's effort level; the session-wide thinking effort. |
| `--model <model>` | yes | `claude --help`: "--model <model>  Model for the current session. Provide an alias for the latest model (e.g. 'fable', 'opus', or 'sonnet') or a model's full name." | Chooses the session's model; the model decides which thinking features exist. |
| `--settings <file-or-json>` | yes | `claude --help`: "--settings <file-or-json>  Path to a settings JSON file or a JSON string to load additional settings from" | Loads settings (incl. `alwaysThinkingEnabled`) for the session. |
| `MAX_THINKING_TOKENS` | yes | binary: `function EWr(){if(process.env.MAX_THINKING_TOKENS)return nc(process.env.MAX_THINKING_TOKENS)>0?null:"MAX_THINKING_TOKENS";let{settings:e}=zb();return e.alwaysThinkingEnabled===!1?"alwaysThinkingEnabled":null}` | Env var read per process; if set to a value > 0, thinking is on. If 0, thinking is off for the whole process. |
| `CLAUDE_CODE_DISABLE_THINKING` | yes | binary env map: `CLAUDE_CODE_DISABLE_THINKING:()=>ar` (present in the environment-variable registry; also appears in the settings/env dump list) | Disables thinking for the process when set (per-process env var). |
| `alwaysThinkingEnabled` | yes | binary settings schema: `alwaysThinkingEnabled:()=>O().optional().describe(...)` and the `EWr()` check above | Settings JSON field; if `false`, thinking is off for the whole session (checked by `EWr()` before any per-call logic). |
| `thinking` (flag/option) | no | `claude --help` contains no `--thinking` flag, no `--effort-level`, no thinking-specific flag beyond the three above | No dedicated thinking flag in the CLI help; thinking is controlled by effort + env vars + settings. |
| `ANTHROPIC_DEFAULT_HAIKU_MODEL` | yes | binary env map: `ANTHROPIC_DEFAULT_HAIKU_MODEL:()=>oI` (present in the environment-variable registry alongside `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`) | The `haiku` alias resolves to whatever this env var is set to; if unset, falls back to the baked-in catalog value `"haiku":"claude-haiku-4-5"`. |

**Effect with the `haiku` alias.** When the session's model is the alias `haiku`, the
model actually used is whatever `ANTHROPIC_DEFAULT_HAIKU_MODEL` is set to; with the
variable unset it is the baked-in `claude-haiku-4-5`. The alias is a pointer, not a
fixed model, and any change of the underlying model (or of the env var) changes what
thinking the session can do. The notes do not verify per-model thinking support, so
whether the resolved model supports thinking is not established here. The alias itself
has no per-call effect: it is fixed for the whole process.

**Per-call switching inside one process: none.** The model capability flags
`per_turn_effort` and `rejects_disabled_thinking` are per-model (catalog-level), not
per-call. The only mid-session change the CLI reports is `per_turn_effort_changed`
(effort down). To switch thinking per API call, a new process (or a new session init)
is required. No env var or flag can toggle thinking per API call inside one process.

## 3. The per-process rule

Everything above is fixed for the whole CLI process:

- `--effort`, `--model` and `--settings` are command-line / startup values, read once
  when the process starts.
- `MAX_THINKING_TOKENS` and `CLAUDE_CODE_DISABLE_THINKING` are environment variables
  read per process. The notes quote `EWr()` reading `process.env.MAX_THINKING_TOKENS`
  and the `alwaysThinkingEnabled` setting; no per-call re-read is exposed.
- `alwaysThinkingEnabled` comes from the settings file the process loads at start.

What **cannot** be done per call inside one CLI process:

- Change the model (no `--model` re-application mid-session).
- Change the effort level mid-session (only a downward change is reported, via
  `per_turn_effort_changed`).
- Toggle thinking on/off between two API calls of the same session (no env var or
  flag is re-read per call; the settings file is not re-read either).
- Change `ANTHROPIC_DEFAULT_HAIKU_MODEL` (or any other model alias) mid-session.

To change any of these, the process must be restarted (a new session init).

## 4. Chosen mapping: per attempt, first attempt = `first[0]`, retry = `retry[0]`

The runner maps a cloud (Anthropic) run's thinking mode **per attempt**, not per call:

- **First attempt** of a task uses the mode of the variant's `first[0]` segment.
- **Retry** (after a review) uses the mode of the variant's `retry[0]` segment.

Because each attempt is one fresh CLI process (the runner starts a new process for
every attempt), the per-process rule of section 3 is satisfied: each process is born
with exactly one mode, and that mode holds for every call of the attempt.

**Why it is safe.** The mode is fixed per process, which the CLI supports. There is no
mid-session switching to do, so nothing races with the CLI's per-process state. The
attempt boundary is the natural seam: the runner already knows when an attempt starts
and ends (the attempt boundary is the same one the local switch uses,
`store.last_completed_worker_finish(...)`), and each attempt is a separate process.

**Why it is implementable.** The runner already builds the CLI command per attempt
(`build_command`) and already knows the attempt type (first vs. retry) and the variant
chosen for the task. It only needs to add the chosen mode to the command it builds:
set the mode's effort/flag values for that process. No mid-session logic is needed,
so no new polling loop or flag-file mechanism is required.

**Why stopping and resuming the session at a call boundary was rejected / deferred.**
Stopping and resuming at a call boundary would let the runner switch modes *inside*
one session, but the CLI does not expose a per-call switch: the only mid-session
change it reports is `per_turn_effort_changed` (effort **down**), and "thinking on"
comes only from a fresh init. Resuming a session does not re-initialize the thinking
state the way a fresh process does, so a resume cannot reliably turn thinking on, and
cannot switch a mode per call. Stopping/resuming is therefore not a per-call switch;
it is a restart, and a restart is exactly what the per-attempt mapping already does.
Deferring it keeps the mapping simple and avoids depending on behaviour the CLI does
not expose.

**Process setting per mode.** Each attempt is one process, so the mode is fixed by the
process environment, using only variables the notes marked present:

- Mode `off`: env `MAX_THINKING_TOKENS=0` (the notes say 0 turns thinking off for the
  whole process), with `CLAUDE_CODE_DISABLE_THINKING` as the alternative.
- Mode `think`: the variable unset, or `MAX_THINKING_TOKENS` set to a value > 0
  (example `8000`, made-up).

It is not verified on a live request what happens to an `off` process when the model
has `rejects_disabled_thinking`; this is left to the implementation tests.

## 5. Windows that are not reproduced

The local policy can express per-attempt timelines as windows
`[mode, calls]` (a `first` window list and a `retry` window list, possibly with
`repeat`). The per-attempt mapping above reproduces only the **first window** of each
list. The following are **not reproduced** by the cloud mapping:

- Later windows after the first in the same `first` or `retry` list
  (e.g. a `["think", 8], ["off", 25], ["think", 10], ["off", null]` timeline only gets
  its first `["think", 8]` window; the later `["off", 25]` / `["think", 10]` windows
  are not applied mid-attempt).
- Call-count boundaries inside an attempt (the `calls` counts that start a later
  window mid-attempt are not honoured; the mode is fixed for the whole attempt).
- The `repeat: true` behaviour (repeating the timeline modulo the total call count)
  is not reproduced.
- A single window with a non-zero call count (e.g. `["think", 8]` in a window whose
  intent was "think for only the first 8 calls, then off") is collapsed to the whole
  attempt: the mode of that first window holds for the entire attempt, so the
  "8 calls" boundary is not honoured.

In short: the cloud mapping applies the **mode of the first window** of `first` (first
attempt) or `retry` (retry), and holds it for the whole attempt. Everything that
depends on call counts, later windows, or repeat is not reproduced.

## 6. Enable setting: `CLAUDE_RUNNER_CLOUD_THINK` (default OFF)

The mapping is controlled by the runner setting `CLAUDE_RUNNER_CLOUD_THINK`:

- **Default: OFF.** When unset (or set to `0`/`false`/`off`), cloud runs use the CLI's
  default thinking behaviour for the chosen model; the runner does not set the mode
  per attempt.
- When ON (`1`/`true`/`on`), the runner applies the per-attempt mapping of section 4:
  first attempt → `first[0]` mode, retry → `retry[0]` mode, each as a fresh process.

This is a new runner-only setting (a `CLAUDE_RUNNER_*` variable, which the runner's
own env whitelist already keeps out of the child process env). It does not change the
CLI's behaviour; it only tells the runner which mode to build into the command for
each cloud attempt.

## 7. Recorded fields and statistics behaviour

**Recorded fields.** The per-run record already stores the policy fields
(`think_policy`, `think_from`, `think_after`, `think_after_steps`, `think_task`); the
chosen variant is derived from the policy text plus the task number, so there is no
separate `variant` column. The mode actually applied to a cloud run is determined by
the attempt type and the variant's `first[0]` / `retry[0]`, as described in section 4.

**Statistics behaviour.** The per-task statistics
(`board_think.py`, `variant_stats` / `thompson_best`) read only the per-step `think`
flag of the attempt rows (`steps[i].think`); they do not read the local flag file or
the event-log mode fields (`thinking_state` / `thinking_switch`). For cloud runs the
`think` flag is derived from the event log's `thinking` / `redacted_thinking` entries,
the same way the local path derives it — so a cloud run's thinking is counted
consistently with a local run, provided the run's event log reaches the archive.

The one gap noted in the T001–T005 notes: the extraction pipeline
(`think_model_extract.py`) today only queries runs with `provider = 'local'`, so a
cloud/Anthropic run is not extracted into the archive at all. If a cloud run were fed
to `variant_stats` directly, its thinking blocks would be counted (the event-log
parsing is provider-agnostic), but today it is excluded before reaching the
statistics. The minimal fix is to widen the provider filter in `think_model_extract.py`
(or add a parallel extract for non-local providers) so the cloud runs' event logs are
archived too.

## 8. Run Manager forwarding verdict

The Run Manager forwards `think_policy` whenever it is set; it never inspects the
provider, so nothing in the n8n workflows limits forwarding to a local provider.
The provider check lives on the runner side, where the thinking switch applies to
runs of the local provider only. Verdict (forwarded for anthropic): **yes**.

- `Constants` (main workflow, line 541) reads the hermes_config columns and emits
  `worker_think_policy`, `worker_think_policy_from_task`, `worker_think_off_after_seconds`,
  `worker_think_off_after_steps`.
- `Build Worker Run Input` (main workflow, line 3965) passes them into the Run Manager
  as `think_policy`, `think_policy_from_task`, `think_off_after_seconds`,
  `think_off_after_steps` (alongside `provider`).
- Run Manager: `Normalize Request` (line 19) keeps the four fields in the state;
  `Start / Resume Run` (line 54), `Resume Existing Session` (line 198) and
  `Start New Session from Handoff` (line 1181) each add the four fields to the POST
  body when any of them is set. The shared condition has no provider/local test.

Runner side (why the policy only acts for local Workers today): `api.py:214-217`
accepts and stores the fields as-is, provider-independent; `runs.py:333` writes the
first-mode event only when `provider_name == "local"`; `think_switch.py:209` iterates
`store.running_local_workers()` (local Workers only), so for an anthropic provider the
forwarded policy is stored on the run record but never applied. Enabling
`CLAUDE_RUNNER_CLOUD_THINK` is what makes the runner apply the per-attempt mapping to
cloud runs instead of leaving them as the CLI default.

Forwarding check (T014): already forwarded for anthropic, so no patch was written.
Evidence in `Claude_Code_Run_Manager_v1.json` (read-only check): the POST-body
condition `nr.think_policy || Number(nr.think_policy_from_task) > 0 || ...` with
`body.think_policy = nr.think_policy` appears in `Start / Resume Run`,
`Resume Existing Session` and `Start New Session from Handoff`, with no provider test.

## 9. Disagreement between the mission text and the CLI

- The mission text implies thinking can be switched per call / per window; the CLI
  does not expose a per-call switch. The per-process rule (section 3) is the actual
  constraint, and the per-attempt mapping is the adaptation to it.
- The mission text's "first attempt / retry" policy and the call-count windows are
  local-policy concepts; the CLI has no notion of a "window" or "call count". The
  mapping collapses each window list to its first window (section 5), so windows
  after the first and call-count boundaries are not reproduced on cloud runs.
- `MAX_THINKING_TOKENS = 0` turns thinking off for the whole process, and a
  non-zero value turns it on — both are per-process, consistent with the per-attempt
  mapping (each attempt is one process with one mode).

No schema change is proposed or required by these facts: the run record already stores
the policy fields and the variant is derivable, and the statistics read the per-step
`think` flag, which cloud runs already produce in their event logs.

## 10. Files of this change

Code (`claude-runner/src/claude_runner/`):

- `cloud_think.py`: the per-attempt mapping (policy parsing, variant choice, `CLOUD_THINK_ENV_VARS`, `CloudThinkSetting`).
- `claude_cli.py`: `build_command` adds the mapping's args; `child_env` passes the per-attempt env vars into the child process.
- `config.py`: the `CLAUDE_RUNNER_CLOUD_THINK` setting (default off).
- `runs.py`: resolves the setting for runs whose provider is not `local` and passes it to `build_command` and `child_env`.

Tests (`claude-runner/tests/`): `test_cloud_think.py`, `test_cloud_think_cli.py`, `test_cloud_think_runs.py`.

Docs and config:

- `docs/cloud-thinking.md`: this document.
- `claude-runner/docs/cloud-thinking-notes.md`: working notes.
- `claude-runner/docs/board-ops-notes.md`: the think-policy section links to this document.
- `.env.example`: `CLAUDE_RUNNER_CLOUD_THINK` is listed as a commented line with no value.
- `CHANGELOG.md`: the 0.8.0 entry.
