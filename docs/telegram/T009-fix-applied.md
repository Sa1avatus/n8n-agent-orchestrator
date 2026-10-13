# T009 — FIX APPLIED: Honest replies and command queue for start/resume/retry/accept/restart

## What was fixed

**1. `QUEUED_VERBS` missing `restart`** (`scripts/patch_status_bot_control.py:46`)
Added `"restart": "bash data-tables/ops/launch_mission.sh %s"` so that confirmed `/restart`
queues a command row instead of writing the live-state key `workflow_status: starting`.
The operator executes the launch line; the bot never launches runs itself.

**2. `pargs.mission` not captured for `restart`** (`scripts/patch_status_bot_control.py:493`)
Added `if (verb === 'restart' && args.length) pargs.mission = args.join(' ').trim();`
so the mission argument from `/restart <mission>` is used in the queue row and launch line
instead of falling back to `cfg.active_mission_id`.

**3. Trailing space in action for zero-arg commands** (`scripts/patch_status_bot_control.py:481`)
Changed `const action = command.replace('/', '') + ' ' + args.join(' ').trim()` to
`const action = command.replace('/', '') + (args.length ? ' ' + args.join(' ').trim() : '')`.
This removes the trailing space in `retry ` (and any zero-arg control command), so the
audit row records `command: "retry"` not `command: "retry "`.

**4. `makeTables` missing `AHAWR Command Queue`** (`tests/n8n-code/run.js:703`)
Added `"AHAWR Command Queue": []` to the table fixture so the insert node's row is
registered in `T.tables` and the e2e assertions can read it back.

**5. `/restart` unit test updated** (`tests/n8n-code/run.js:513`)
The old test asserted `/restart` writes `workflow_status: starting` to the live state.
Since `/restart` now queues, the test now asserts `r.queued === true && r.table === 'AHAWR Command Queue'`.

## Acceptance criteria — all met

| # | Criterion | Verified by |
|---|-----------|-------------|
| 1 | No bot reply contains 'approved' for an action not performed | All e2e tests assert `!res.replies[0].text.includes("approved")` for /start, /resume, /restart, /retry, /accept |
| 2 | Queued commands reply with exact launch line | e2e tests assert exact string `записано в очередь команд, исполнитель: оператор, команда: bash data-tables/ops/launch_mission.sh <mission>` for /start, /resume, /restart |
| 3 | Queue/flag rows written by dataTable nodes and audited | e2e tests assert `res.trace.includes("Write Command Queue")` and `T.tables["AHAWR Command Queue"].length === 1` for /start; `res.trace.includes("Write Autonomous Agent Live State")` and flag value for /retry, /accept; audit rows have `result: "approved"` |
| 4 | Tests cover reply text and written row for /start and /accept | Section 10 tests (n)/(o) in `tests/n8n-code/run.js` |

## Verification

```
$ python scripts/patch_status_bot_control.py
rewrote to control bot; 39 nodes. Checks passed

$ node tests/n8n-code/run.js
TOTAL: 195 passed, 0 failed

$ grep -n "approved" AHAWR_Status_Bot.json
# All hits are code variable names or audit result values, not reply text
```
