# QA layers: design

This document specifies how QA runs in layers and how their results form one verdict. It builds on
[qa-gate.md](qa-gate.md) (the gate and its fail-open table), [qa-smoke.md](qa-smoke.md) (the smoke crawl)
and [qa-container.md](qa-container.md) (where QA runs).

## Layers

QA runs up to three layers in one run. Each layer produces its own verdict, and the layers are then
combined into one verdict for the mission.

| Layer | What it checks | Where it runs |
| --- | --- | --- |
| `regression` | pytest, node, ruff and mypy of the project, compared with the project baseline | read-only mount of the project, in the QA container |
| `api` | the API routes of the dashboard, against the in-process stack | in-process stack in the QA container |
| `ui` | the stored scenarios; plus the smoke crawl when `options.smoke` is true and `qa_smoke.py` exists | the QA stack dashboard, or the project's own app on `127.0.0.1` when `options.project_apps` is true (see *Project apps*) |

A request without `layers` runs only `ui`, with the scenarios and smoke rules that exist today.

## Combined verdict

The request payload carries `layers`, the list of layers to run. The verdict is one list with one
entry per layer that ran, in run order. Every entry carries a `layer` field:

```json
[
  {"layer": "regression", "scenario": "regression", "verdict": "fail", "reason": "new failure tests/test_x.py::test_y"},
  {"layer": "ui", "scenario": "board", "verdict": "pass"}
]
```

Each verdict entry is judged by the unchanged fail-open / fail-closed table of
[qa-gate.md](qa-gate.md). A failing layer does not skip the next layers: the remaining layers still
run and report their own verdicts.

QA Review names the failed layer in `next_task`, for example
`QA regression failed: new failure tests/test_x.py::test_y`.

A request without `layers` is `[ui]` and produces exactly the old output: the old verdict shape, with
no `layer` field. QA Review reads a verdict without a `layer` field as `ui`, so existing missions
behave as before.

## Rule table

Every layer outcome has one effect on the mission. Only `fail` returns the mission for changes, and
at most `QA_MAX_RETRIES` times (2, as in [qa-gate.md](qa-gate.md)). All other outcomes accept the
mission and never reject it.

| # | Layer outcome | Meaning | Effect on the mission | Notice |
| --- | --- | --- | --- | --- |
| 1 | `pass` | the layer ran and met its expected results | accept | `QA passed` |
| 2 | `fail` (`retries < QA_MAX_RETRIES`) | the layer found a regression or a broken route or screen | needs_changes review; the retry counter +1 | `QA failed` with the layer and step |
| 3 | `fail` (`retries = QA_MAX_RETRIES`) | the same, after the retries | accept | `QA still failing after the retries` |
| 4 | `skipped` | nothing in scope changed, or the project has no QA profile | accept | none (silent) |
| 5 | `blocked` | a precondition is missing: a folder, a git repository, a command the profile names, Playwright, or the app of the project that did not start or never became healthy | accept | `QA could not run` with the reason |
| 6 | `error` | the layer crashed, or the worker reported an error | accept | `QA could not run` with the reason |
| 7 | `timeout` | the layer hit its time limit (see Constants) | accept | `QA could not run` (timed out) |
| 8 | missing worker | no heartbeat, and nothing claims the request | accept | `QA could not run` (no worker) |
| 9 | missing profile | the project has no entry in `qa/projects.yaml` | accept as `skipped`, reason `no QA profile` | none (silent) |

A project without a QA profile is `skipped` with the reason `no QA profile`. It is never `blocked` and
never a `fail`: a missing profile is a setup choice, not a broken build.

## Constants

| Name | Value | Unit | Bounds |
| --- | --- | --- | --- |
| `REGRESSION_MINUTES` | 25 | minutes | the regression layer; a layer that runs past it is `timeout` |
| `API_MINUTES` | 5 | minutes | the api layer; a layer that runs past it is `timeout` |
| `UI_MINUTES` | 20 | minutes | the ui layer, scenarios and smoke together; a layer that runs past it is `timeout` |
| `QA_CLAIM_WAIT_MINUTES` | 5 | minutes | existing: a `queued` request expires after it |
| worker run timeout | 20 | minutes | existing: the outer bound of a claimed run |

The whole request keeps the existing outer bound of [qa-gate.md](qa-gate.md): the claim wait plus the
worker run timeout. The per-layer timeouts apply to each layer separately.

## Operator setting

The operator turns layers on in `roles_json`, under the `qa` options. `mode` is unchanged.

```json
{"qa": {"1": "anthropic/sonnet", "options": {"mode": "final", "layers": ["regression", "api", "ui"]}}}
```

- `layers` is a list of layer names. The allowed names are `regression`, `api` and `ui`. A missing or
  empty list means `["ui"]`, which is exactly the behaviour of the QA gate before layers.
- Unknown names are rejected by the roles validators, `roles_config.py` and `board_roles.js`, which
  share one cases file, `roles_cases.json`.
- `smoke` is read by the `ui` layer only, and only when `qa_smoke.py` exists; see
  [qa-smoke.md](qa-smoke.md).
- `project_apps` is a boolean, default `false`, and makes the `ui` layer run the project's own app;
  see *Project apps: the `project_apps` flag*.

## Project profiles: `qa/projects.yaml`

Each project that the regression layer may run has one entry. The profile is matched by a
`working_directory` prefix, compared with the normalised host path of the mission's project.

```yaml
projects:
  - name: ahawr-runner
    working_directory: /d/n8n/ahawr-runner
    regression:
      - name: pytest
        command: python -m pytest -q --no-cov -p no:cacheprovider
      - name: node
        command: node --test tests/*.test.js
      - name: ruff
        command: python -m ruff check src tests
      - name: mypy
        command: python -m mypy src
    baseline: qa/baselines/ahawr-runner.json
```

- `working_directory` is a prefix of the normalised host path, ending in `ahawr-runner`. The longest
  matching prefix wins.
- `regression` lists the commands in run order, each run from `ahawr-runner/`. Each command has a
  `name`, which is the tool key of the baseline.
- `baseline` is a path, under the project, of the baseline file.

## The `ui` block of a project profile

`ui` is the only optional key of a profile: `OPTIONAL_PROFILE_KEYS = frozenset({"ui"})`
(`qa_regression.py:190`). A profile without it has `ui = None` (`qa_regression.py:273`), and the `ui`
layer then runs the qa child, as before the flag existed.

All six keys are required — there are no defaults in the block — and an unknown key and a missing key
both raise `ProfileError` (`qa_regression.py:281-286`).

| Key | Type | Bounds, as the parser states them | Source |
| --- | --- | --- | --- |
| `start_command` | non-empty list of non-empty strings | a shell string is refused: `ui start_command must be a non-empty list of arguments, not a shell string` | `qa_regression.py:297-304` |
| `env` | map of string to string | a value may use only `{port}` and `{data_dir}`: `UI_PLACEHOLDERS = frozenset({"port", "data_dir"})`, `ui env <key> uses unknown placeholder(s) ... allowed are ['port', 'data_dir']` | `qa_regression.py:204-205`, `307-323` |
| `port_env` | non-empty string | must match `VARIABLE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")`, else `ui port_env must be a variable name, got <name>` | `qa_regression.py:203`, `326-330` |
| `health_path` | non-empty string | must start with `/`: `ui health_path must start with '/': <path>` | `qa_regression.py:333-337` |
| `ready_timeout_seconds` | number; a bool is refused | `READY_TIMEOUT_MIN_SECONDS = 1` and `READY_TIMEOUT_MAX_SECONDS = 300` seconds, stored as a float: `must be between 1 and 300 seconds, got <value>` | `qa_regression.py:201-202`, `340-348` |
| `scenarios_dir` | string | relative to `qa/scenarios/`: no leading `/`, no drive letter (`DRIVE`, `qa_regression.py:200`), no `..` segment: `must be relative and contain no '..'` | `qa_regression.py:351-358` |

The mi-find-tracker profile of `qa/projects.yaml` is the working example (trimmed):

```yaml
    ui:
      start_command: ["python", "-m", "mifind"]
      env:
        PYTHONPATH: src
        DB_PATH: "{data_dir}/mifind.db"
        BIND_ADDRESS: "127.0.0.1"
      port_env: HOST_PORT
      health_path: /health
      ready_timeout_seconds: 30
      scenarios_dir: mi-find-tracker
```

`{port}` and `{data_dir}` are substituted in the `start_command` arguments and in the `env` values
(`qa_app.py:140-141`), and `port_env` is set to the chosen port (`qa_app.py:132-137`).

## Scenario `target` and per-project scenario folders

- `target` is a top-level scenario key (`TOP_LEVEL_KEYS`, `qa_runner.py:61-66`). The legal values are
  `dashboard` and `app` (`DASHBOARD_TARGET = "dashboard"`, `APP_TARGET = "app"`,
  `TARGETS = frozenset({DASHBOARD_TARGET, APP_TARGET})`, `qa_runner.py:58-60`); any other value is
  refused by `_target` (`qa_runner.py:154`, `160-164`).
- The default is `dashboard`: `target: str = DASHBOARD_TARGET  # dashboard: the QA stack; app: the
  handle passed to the runner` (`qa_runner.py:136`). Such a scenario runs against the throwaway QA
  stack, exactly as before (`qa_runner.py:486-491`).
- `target: app` drives the scenario against the handle the caller started with `qa_app.app_up`
  (`qa_runner.py:481-485`). With no handle the verdict is `blocked`, reason
  `target app: no app handle was given` (`qa_runner.py:481-483`).
- A project's scenarios are read only from `qa/scenarios/<scenarios_dir>/`:
  `load_project_scenarios(scenarios_dir, base_dir=SCENARIO_DIR)` (`qa_runner.py:443-445`), with
  `SCENARIO_DIR = qa_stack.CLAUDE_RUNNER_DIR / "qa" / "scenarios"` (`qa_runner.py:88`). An empty
  folder is `blocked`, reason `no scenario files (*.yaml) in qa/scenarios/` (`qa_runner.py:436-439`,
  reached from `qa_worker.py:417-420`). `qa/scenarios/mi-find-tracker/` holds the four tracker
  scenarios.
- Unknown, not covered by the code: `qa nightly` loads the shared `qa/scenarios/` folder for the `ui`
  layer of every profile (`qa.py:207`, `392-393`. It never starts a project app and never reads a
  profile's `scenarios_dir`.

## New scenario steps (T007)

`viewport`, `color_scheme` and `no_horizontal_scroll` are parsed in `qa_runner.py` and work against
either target.

- `viewport` is a step with the keys `width` and `height` (`_viewport_step`, `qa_runner.py:287-291`).
  Each is an integer, a bool is refused, and it must sit inside `VIEWPORT_MIN_PX = 100` and
  `VIEWPORT_MAX_PX = 4_000` (`qa_runner.py:73-74`); outside them the scenario fails to parse with
  `<field>: <value> px is outside 100..4000 px` (`qa_runner.py:294-299`). It runs as
  `page.set_viewport_size` (`qa_runner.py:672`).
- `color_scheme` is a step whose value is one of `dark`, `light` (`COLOR_SCHEMES =
  frozenset({"dark", "light"})`, `qa_runner.py:56`); anything else is refused: `color scheme must be
  one of: dark, light, got <scheme!r>` (`qa_runner.py:359-361`). It runs as
  `page.emulate_media(color_scheme=...)` (`qa_runner.py:674`).
- `no_horizontal_scroll` is an expectation, default `False` (`no_horizontal_scroll: bool = False`,
  `qa_runner.py:111`; parsed at `qa_runner.py:388-389`, checked in `_check_no_horizontal_scroll`,
  `qa_runner.py:804-805`, `841`).

## Project apps: the `project_apps` flag

| Key | Where | Type | Default |
| --- | --- | --- | --- |
| `project_apps` | the `qa` options of `roles_json` | boolean | `false` |

- It is an allowed key of the `qa` role: `QA_OPTION_KEYS = COMMON_OPTION_KEYS | {"mode", "max_minutes",
  "max_cost_usd", "layers", "smoke", "project_apps"}` (`roles_config.py:17-24`).
- A non-boolean is rejected: `role 'qa': option 'project_apps' must be true or false`
  (`roles_config.py:195-196`).
- It is read back as `bool(options.get("project_apps", False))` (`roles_config.py:240-241`): an absent
  key reads as `false`.
- The board role editor has no row for it: `QA_OPTION_KEYS` in `board_roles.js:11` lists `mode`,
  `max_minutes`, `max_cost_usd` and `layers` only, so the flag lives in the free-text roles JSON.

**How the flag chooses the path** (`qa_worker.py:436-450`):

- `_ui_app_target` returns `None` when the flag is off, when the request has no working directory, when
  `qa/projects.yaml` cannot be read, when no profile prefix matches the mission, or when the matched
  profile has no `ui` block (`qa_worker.py:360-383`). The `ui` layer is then
  `_layer_command("ui", ["run"], working_directory)` — the qa child, unchanged (`qa_worker.py:440-441`).
- With the flag on and a `ui` block, `_app_ui_runner` starts the app with `qa_app.app_up`, runs the
  project scenarios with `run_browser_scenario(..., app=handle)`, and calls `app_down` in a `finally`
  on every path, blocked or raised alike (`qa_worker.py:386-405`, `qa_app.py:59-113`). The smoke crawl,
  when `options.smoke` is true, runs against the same handle (`qa_worker.py:427-432`). The `regression`
  and `api` layers are untouched.

**Rollback:** set `"project_apps": false`, or drop the key, in the `qa` options. With the flag off
`_ui_app_target` returns `None` at `qa_worker.py:363` before any profile is read, so the `ui` layer
takes the qa child path. No restart or container change is involved, and the `ui` layer behaves as it
did before the flag existed.

## Limits of a project app

| Limit | What the code guarantees | Source |
| --- | --- | --- |
| loopback only | `ALLOWED_APP_HOSTS = frozenset({QA_LOOPBACK_HOST, "localhost"})`, that is `127.0.0.1` and `localhost`; the port comes from `_free_port()`, which binds on `127.0.0.1` | `qa_app.py:41-42`, `69`; `qa_stack.py:29`, `568-571` |
| no Docker for project apps | the app is the profile's argument list run with `subprocess.Popen`; no container is built, pulled or started | `qa_app.py:75-80`; the module docstring, `qa_app.py:6-7`: "No Docker" |
| scrubbed environment | the child keeps only `OS_BASIC_ENV_NAMES = {"PATH", "PATHEXT", "SystemRoot", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE"}` plus the profile `env` and `port_env` set to the port | `qa_app.py:44-46`, `132-137` |
| throwaway data dir | `<system temp>/qa-app-<8 hex>` (`APP_DATA_PREFIX = f"{QA_PREFIX}app-"`, `APP_RUN_ID_BYTES = 4`), removed by `app_down` | `qa_app.py:47-48`, `70-71`, `109-113` |
| bounded health wait | `max(1, ready_timeout_seconds // HEALTH_POLL_SECONDS)` polls, delay `HEALTH_POLL_SECONDS = 2` seconds, request cap `HEALTH_REQUEST_SECONDS = 3` seconds, status must be `200` | `qa_app.py:95-101`, `127-129`, `144-150`; `qa_stack.py:63-64` |
| layer bound | the `ui` layer keeps `UI_TIMEOUT_MINUTES = 20` minutes, passed to the scenarios and the smoke crawl as `max_minutes` | `qa_worker.py:69`, `73`, `416` |
| one app at a time | each layer runs in a `ThreadPoolExecutor(max_workers=1)`, and the worker claims one request at a time | `qa_worker.py:485`; see *Not done* at the end of this file |

Blocked outcomes of the app path — accepted by the gate, never a `fail` (rule 5):

| Reason | Source |
| --- | --- |
| `app binary 'python' is not installed: ...` | `qa_app.py:81-83` |
| `app start command 'python' could not run: ...` | `qa_app.py:84-86` |
| `app 'mi-find-tracker' at http://127.0.0.1:<port>/health is not healthy after 15 polls` | `qa_app.py:97-101`, `127-129`; `qa_stack.py:507-513` |
| `app host '<host>' is not loopback; allowed hosts: 127.0.0.1, localhost` | `qa_app.py:116-124` |
| `target app: no app handle was given` | `qa_runner.py:481-483` |
| `no scenario files (*.yaml) in qa/scenarios/` | `qa_runner.py:436-439` |
| Playwright not installed (`PLAYWRIGHT_HINT`) | `qa_runner.py:91-95`, `477` |

When the app never becomes healthy, `app_up` kills the process tree and removes the data directory
before raising (`qa_app.py:95-106`, `109-113`).

## Operator steps for project apps

The QA worker runs in the `ahawr-qa` container (`qa/Dockerfile.qa`), which already has Playwright,
Chromium and the project tools. The container runs the code from the read-only mount
(`PYTHONPATH=/d/n8n/ahawr-runner/src`), so a source change needs `docker restart ahawr-qa`, not a rebuild.
The host worker (`_start_qa_worker.ps1`) is not used.

1. `docker restart ahawr-qa` after the code is in the working tree (done 2026-11-07).
2. Set `"project_apps": true` in the `qa` options of `roles_json` (done 2026-11-07, hermes_config row 5).
   Rollback: set it to `false`.

Prerequisites the mission did not stage or check: the project checkout at the profile's
`working_directory` (`/d/ClaudeProjects/mi-find-tracker`) and `python -m mifind` runnable there. The two
curl dumps named in the profile `env` do not exist, which is the shortcut documented in
`qa/projects.yaml:32-35`: the poll thread never reaches Xiaomi.

## Baseline format

The baseline records the failures that already existed before a change, per tool. A regression is a
failure whose key is not in the baseline. The file is written by the operator with `qa baseline <project>`.

```json
{
  "project": "ahawr-runner",
  "taken_at": "2026-10-22T09:00:00Z",
  "failures": {
    "pytest": ["tests/test_board_pg.py::test_update_mission_empty_fields_raises_valueerror"],
    "node": [],
    "ruff": ["src/ahawr_runner/board_think.py:E501"],
    "mypy": ["src/ahawr_runner/board_pg.py:41: error: Argument 1 has type ..."]
  }
}
```

- `failures` has one list per tool name from the profile. Each entry is a stable failure key: the test
  node id for pytest and node, and `path:code` for ruff; for mypy, `path:line: error: message`.
- The regression layer passes when every failure key of a tool is in that tool's baseline list. A key
  not in the list is a `fail`.

## Regression safety rules

- The project is mounted read-only.
- The regression layer does not fix code, run a formatter, or install packages.
- Each command runs in its own process group. On timeout the whole process tree is killed.
- The only writes are under `/qa-runs`.
- A command that is not found on the worker makes the layer `blocked`, not `fail`.

## CLI

All commands run inside the QA container, through `python -m ahawr_runner.qa`.

| Command | Purpose |
| --- | --- |
| `qa regression` | run the regression layer for the current project and print its verdict |
| `qa baseline <project>` | run the regression commands of `<project>` and write its baseline file; operator only |
| `qa api` | run the api layer and print its verdict |
| `qa smoke` | crawl the board in a browser and print the verdict |
| `qa nightly` | run every layer of every project profile it finds, in order, and write the one nightly verdict file with a new `layers` key |

`qa smoke` flags: `--url` (base URL; without it a throwaway stack is started), `--lang` (interface
language of the crawl, `en` or `ru`; default both), `--allow-host NAME` (host name the crawl may run
against, needed when `--url` is not local), `--max-pages`, `--max-clicks-per-page` and
`--max-minutes` (the crawl bounds, default 40 / 25 / 10). Without `--lang` the crawl runs in both
languages and the verdict is `blocked` if any crawl is blocked, `fail` if any crawl fails, and `pass`
only when both pass; the findings of both crawls are kept, each tagged with its `lang`.

## Nightly verdict file

`qa nightly` writes `qa/nightly-verdict.json` with two keys, and a third when smoke is on:

- `scenarios` – the verdict list from the `ui` scenarios, in the same order the old file held.
- `layers` – the layer verdicts, one entry per (profile, layer), in the order `regression`, `api`, `ui`,
  for each profile in `qa/projects.yaml` (at most `MAX_NIGHTLY_PROFILES` = 20).
- `smoke` – the smoke-crawl verdict, present only when the `smoke` option of the `qa` role in
  `hermes_config.roles_json` is `true`.

The exit code is `0` only when every verdict in the lists (and in `smoke`, when present) is `pass`.
The board reads the `scenarios` key; an old file that is still a bare list is read as `scenarios`.

```text
$ qa nightly
nightly verdict: /d/n8n/ahawr-runner/qa/nightly-verdict.json
```

```json
{
  "scenarios": [
    {"scenario": "board", "verdict": "pass"}
  ],
  "layers": [
    {"layer": "regression", "scenario": "regression", "verdict": "pass"},
    {"layer": "api", "scenario": "GET /api/board/queue", "verdict": "pass"},
    {"layer": "ui", "scenario": "board", "verdict": "pass"}
  ]
}
```

## Not done

- No scheduling. `qa nightly` runs when the operator starts it.
- No data-table writes. Verdicts reach the mission through the existing QA Review path, not through a data
  table column.
- The baseline is run by the operator. The QA layers never write a baseline.
- Project apps run only through the QA worker (the `ahawr-qa` container). `qa nightly` and the `qa` CLI have no app path: no CLI
  flag starts a project app, and nightly uses the shared scenario folder
  (see *Scenario `target` and per-project scenario folders*).
- No bound on the number of scenarios in a project folder — `qa_worker.py:417-420` loads whatever the
  folder holds. Unknown in the code, not a limit the design set.
- One project app per `ui` layer run: the app starts inside that layer and is torn down before the next
  layer, so no two project apps run at once.
