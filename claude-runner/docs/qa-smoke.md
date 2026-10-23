# QA smoke crawl: design

The smoke crawl is a second QA layer that needs no per-mission scenario. It opens the dashboard in
the throwaway stack, follows what the page offers, clicks within a budget, and reports what looks
wrong. It runs next to the scenario suite described in `qa-acceptance.md`; it does not replace it.
Status: design only. Nothing in this document is implemented yet.

## Three layers

1. **Regression set.** The stored scenarios in `claude-runner/qa/scenarios` (14 today, the
   0.7.400 regression list). They check known behaviour step by step and are run with
   `python -m claude_runner.qa run`.
2. **Smoke crawl** (this document). Scenario-less. It visits pages reachable from the dashboard,
   clicks buttons and links within a budget, and applies the detectors below to every request,
   response and console line. It finds failures nobody wrote a scenario for, but it knows no
   expected result, so it can only flag what is wrong on the wire or on screen.
3. **Task acceptance `ui_check`.** A scenario that the Architect writes into one task's plan. It
   runs in the `ui_tasks` mode and is judged against that task's stated objective (see
   `qa-acceptance.md`).

Layer 1 knows the expected results; layer 2 knows the generic failures; layer 3 knows one task's
intent. A failure found by layer 2 is still reported in the same verdict shape and is written as a
review the same way as any QA fail.

## Detectors

Each detector is a pure check on data the runner already records (`_Recorder` in `qa_runner.py`
keeps `console_errors`, `network_errors` and `responses`). Each finding names its detector.

| Detector | Source | Severity | Rule |
|---|---|---|---|
| `console-error` | page console | error | a console line of type error |
| `server-error` | responses | error | a same-origin response with status >= 500 (`SERVER_ERROR_MIN`) |
| `client-error` | responses | error | a same-origin response with status >= 400, except the allowed set |
| `conflict` | responses | warning | a 409; a legal answer to a version conflict, so it is reported, not failed |
| `missing-header` | `_check_header` | error | a POST, PUT or DELETE under the base URL without `X-AHAWR-Dashboard: 1` |
| `null-id` | request path | error | a URL carrying a literal `null` or `undefined` (a path segment such as `/null`, a query value such as `=null` or `=undefined`) or a literal object text such as `[object Object]` (`%5Bobject`) |
| `error-visible` | DOM | error | after an action, error text that appears only in a hidden or `display:none` element is a finding (`VISIBLE_ONLY`, `visible=true`) |
| `bad-text` | DOM | error | visible text that is or contains `undefined`, `null`, `NaN` or `[object Object]`, or an i18n key shape such as `conflict.plan_changed` shown in one language while the other language has a translation |
| `overflow` | DOM | error | a visible element wider than the viewport, or zero-size inside a visible container |
| `asset-failed` | requests | error | an image or stylesheet request that failed |
| `pageerror` | page | error | an uncaught page error (the page `pageerror` event) |
| `no-effect` | page before/after a click | warning | a click on a non-disabled button that changes nothing on the page |

`error-visible` checks visibility, not presence: an error text inside a hidden element counts as
no message. This is the rule that catches a notice written into a hidden panel.

The crawl runs in `en` and `ru` and compares the visible texts of the two languages for
missing translations; a text shown in one language and not in the other is what `bad-text`
flags.

## Bounds

Every crawl stops at the first bound it reaches. The defaults are:

- `max_pages` 40: distinct pages (URL without hash is the page identity; hash views count once each).
- `max_clicks_per_page` 25: clicks on one page, including links and tabs.
- `max_minutes` 10: wall-clock for the whole crawl, measured from the start of the run.

All three are overridable by flags of `python -m claude_runner.qa smoke`: `--max-pages`,
`--max-clicks-per-page` and `--max-minutes`. Reaching a bound is not a failure; the verdict notes
it as `bound_reached` in the summary. Only a crawl that stops before its first page loads is
`blocked`. Each element click waits at most `STEP_TIMEOUT_MS` (15 s), as the scenario steps do.

## Destructive list

The crawler never clicks a control whose label matches, and never requests a path that matches,
an entry of `claude-runner/qa/smoke_seeds.yaml`. The list is data, not code: a new word is a line
in the file. Entries are matched case-insensitively, in English and Russian, for example labels
`delete`, `remove`, `reset`, `drop`, `restart`, `stop`, `archive`, `clear` and paths such as
`/api/board/queue/restart`. A control that matches is recorded in the summary as skipped, with the
entry that matched it.

## Host guard

The crawl runs only against a dashboard whose host is one of:

- `127.0.0.1` or `localhost`;
- a container of the throwaway stack, whose name starts with `qa-`.

Any other host, including the production dashboard, is refused before the browser starts, and the
verdict is `blocked` with the reason. `--allow-host NAME` adds one host name for one run; it is an
explicit operator choice and is never read from `roles_json`. The host is parsed with `urlsplit`,
not by matching the URL text.

## Verdict shape

The smoke verdict keeps the fields of `build_verdict` and adds `findings`, which `build_verdict`
does not produce today:

```json
{
  "scenario": "smoke",
  "verdict": "fail",
  "blocked_reason": null,
  "findings": [
    {"severity": "error", "detector": "error-visible", "page": "/static/board.html#board/kanban",
     "reason": "no visible error text after a 409 on PUT /api/board/queue/move"}
  ],
  "pages": 12,
  "clicks": 74,
  "skipped": [{"label": "Delete", "matched": "delete"}],
  "bound_reached": null,
  "screenshots": [],
  "console_errors": [],
  "network_errors": [],
  "minutes": 6.2,
  "cost_usd": 0.0
}
```

- `pass`: the crawl ran and has no `error` finding. `warning` findings do not change the verdict.
- `fail`: at least one `error` finding. Each finding carries its page and the detector that fired.
- `blocked`: the stack or browser did not start, the host guard refused the host, or the crawl
  stopped before its first page loaded. Blocked is never a pass.

The runner calls no model, so `cost_usd` stays 0.0 for the crawl.

## Activation

Smoke is off by default. It is enabled per role, under the `qa` options of `roles_json`:

```json
{"qa": {"1": "anthropic/sonnet", "options": {"mode": "final", "smoke": true}}}
```

- `mode` must not be `off`; `smoke: true` adds the crawl after the regression set in the same run.
  The trigger is the trigger of the mode: `final` runs at the end of a mission.
- `smoke` defaults to `false`. With `mode` `off`, or with `smoke` absent or `false`, the crawl never runs
  and the behaviour is unchanged.
- The validator accepts `smoke` as a boolean: `QA_OPTION_KEYS` in `roles_config.py:17` holds
  `mode`, `max_minutes`, `max_cost_usd`, `layers` and `smoke`; a non-boolean `smoke` is
  rejected with the reason `option 'smoke' must be true or false`.

## Rollback

Set `"smoke": false` (or remove the key), or set `mode` to `off`. No code is reverted and no
workflow is patched. The crawl changes nothing outside the throwaway stack; its containers, images,
volumes and networks with the `qa-` prefix are removed by `qa_stack.stack_down`, and
`docker ps -a` is checked after the run. If a run is interrupted, the same teardown removes the
stack.

## Six real findings

Found in the first real run on the host (`reports/ahawr-qa-acceptance-report-ru.md`, section 5).
The column "found by crawl" says whether the crawl as designed above would have flagged the finding
on the 0.7.400 dashboard. The rows are in the order of the objective; their numbers in the report
are 6, 7, 2, 1, 3 and 8.

| Finding | Found by crawl | Detector | Why |
|---|---|---|---|
| Null URL: scenarios opened `/board#...`, which does not exist (should be `/static/board.html#board/...`) | no | none | The bad address was in a scenario file, not a link the app renders; the crawl follows app links and never requests `/board#`. Layer 1 catches it. |
| Missing n8n data tables: the stack answered 503 on every API request | yes | `server-error` | The first page load already sends API requests that return 503 (`SERVER_ERROR_MIN`). |
| Null roles URL: `GET /api/board/missions/null/roles` when the settings window opened fast | yes | `null-id` (and `client-error`) | The request path carries a literal `null` mission id; the detector reads the path, not the status. |
| Hidden `#boardstate` message: the Kanban conflict message was written to a hidden element | yes | `error-visible` | After the 409 the error text exists in the DOM but not on screen; a presence check passes it, the visibility check fails it. |
| First save 409: saving mission settings with no `board.mission_config` row answered 409 "plan version is required" | no | none (`conflict` warning only) | A 409 is a legal answer to a version conflict, so the crawl records it as a warning, not an error. Whether this 409 is wrong depends on whether a row exists, which the crawl does not know; only a scenario with that fixture can judge it (`ALLOWED_HTTP_STATUSES` in layer 1). |
| Card-order timing: the order check ran once, right after the drag, before the board re-rendered | no | none | The crawl has no expected order to compare with; a card that has not moved yet cannot be told from a wrong order. The fix (poll the order until `STEP_TIMEOUT_MS`) belongs to the regression layer, where the `order` expectation is written. |

Summary: three of the six are found by the crawl (rows 2, 3 and 4 above); three are not (rows 1, 5
and 6), for the reasons in the table. Rows 5 and 6 are caught only by a scenario that knows the
expected state.
