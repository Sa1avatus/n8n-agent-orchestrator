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
| `ui` | the stored scenarios; plus the smoke crawl when `options.smoke` is true and `qa_smoke.py` exists | in-process stack in the QA container |

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
| 5 | `blocked` | a precondition is missing: a folder, a git repository, or a command the profile names | accept | `QA could not run` with the reason |
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

## Project profiles: `qa/projects.yaml`

Each project that the regression layer may run has one entry. The profile is matched by a
`working_directory` prefix, compared with the normalised host path of the mission's project.

```yaml
projects:
  - name: claude-runner
    working_directory: /d/n8n/claude-runner
    regression:
      - name: pytest
        command: python -m pytest -q --no-cov -p no:cacheprovider
      - name: node
        command: node --test tests/*.test.js
      - name: ruff
        command: python -m ruff check src tests
      - name: mypy
        command: python -m mypy src
    baseline: qa/baselines/claude-runner.json
```

- `working_directory` is a prefix of the normalised host path, ending in `claude-runner`. The longest
  matching prefix wins.
- `regression` lists the commands in run order, each run from `claude-runner/`. Each command has a
  `name`, which is the tool key of the baseline.
- `baseline` is a path, under the project, of the baseline file.

## Baseline format

The baseline records the failures that already existed before a change, per tool. A regression is a
failure whose key is not in the baseline. The file is written by the operator with `qa baseline <project>`.

```json
{
  "project": "claude-runner",
  "taken_at": "2026-10-22T09:00:00Z",
  "failures": {
    "pytest": ["tests/test_board_pg.py::test_update_mission_empty_fields_raises_valueerror"],
    "node": [],
    "ruff": ["src/claude_runner/board_think.py:E501"],
    "mypy": ["src/claude_runner/board_pg.py:41: error: Argument 1 has type ..."]
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

All commands run inside the QA container, through `python -m claude_runner.qa`.

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
nightly verdict: /d/n8n/claude-runner/qa/nightly-verdict.json
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
