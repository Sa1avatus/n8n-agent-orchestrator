# Adaptive split: plan gate, no-truncate parts, split on timeout or bloat

Status: implemented in the ops scripts and runner, all new behaviour behind flags that are OFF by default.
The live n8n workflow is unchanged until the patch is applied and the flags are turned on.

## Parts

1. **Plan gate.** Before an Architect plan is stored, `data-tables/ops/adaptive_split/plan_gate.js`
   (PlanGate IIFE: `detectWideTasks`, `planParts`, `legacyCap`, `buildPartMissions`) checks each task.
   A task is wide when it names more than 3 scope files, has an objective longer than 1500 characters,
   has whole/entire/completely wording near a module or file (English or Russian), or has more than 4
   deliverables. With the flag off the gate only warns.
2. **No-truncate parts.** The old path cut the plan silently to a fixed cap. With `plan_no_truncate`
   on, a plan of up to `max_tasks` (15) tasks runs whole. A bigger plan is split at phase boundaries
   into parts of up to `max_tasks` each, and a part may be extended up to `max_tasks_hard` (30)
   so that a phase is not cut in the middle. A plan that would need more than 5 parts is refused,
   not cut. Code: the Evaluate Architect node (`noTruncate` branch) and
   `added/Create_Part_Missions.js` (`planParts(tasks, max_tasks, max_tasks_hard ?? 30, 5)`).
3. **Adaptive split at run time.** `shouldSplit()` in the Build Worker Run Input node decides, before
   the RESUME prompt is built, whether a task is replaced by narrower tasks. The Architect is asked for
   a split in split mode with a digest of the failed run. The task is replaced by 2-4 tasks with
   letter-suffixed ids built from the original id (T013 becomes T013A, T013B, T013C; never
   renumbered, no next free number) and rewritten dependencies, and the Worker session fields are
   reset. Code:
   `added/Apply_Split_Answer.js` and the test in `task_split.test.js` (which builds the patched
   scratch copy in-process with `adaptive_split_patch.py` and loads the nodes from it).
4. **Run hygiene.** With `run_hygiene` on, the workflow cancels the previous run of a session before a
   new one starts (`Has Cancel Run Id` -> `Cancel Previous Run`), so relaunches do not leave stale runs.
   The runner side of this is the supersede and watchdog behaviour in `ahawr-runner/README.md`.

Part dependencies: with the flag `mission_dependencies` on, each created part carries
`depends_on` of the part before it — part 2 waits for the original mission, part 3 for
`<mission_id>-p2`, part 1 for nothing (`data-tables/ops/split_dependencies_patch.py`, anchor
`plan: partPlan(part, k),` in `Create Part Missions`), so the queue never picks a part before the
part it waits for. Details in `docs/mission-dependencies.md`.

## Split-trigger decision table

`shouldSplit()` in the Build Worker Run Input node is evaluated on the last Worker run of the task.
The rows are checked top to bottom; the first match decides.

| # | Condition                                                                 | Result                          |
|---|---------------------------------------------------------------------------|---------------------------------|
| 1 | `adaptive_split` is 0                                                     | No split (behaviour unchanged)  |
| 2 | The task is already a split part (`split_depth` >= 1)                     | No split                        |
| 3 | The mission already has `max_splits` (6) splits                           | No split                        |
| 4 | One more split would exceed `max_total_tasks` (40)                        | No split                        |
| 5 | Last run error code is `timeout`                                          | Split                           |
| 6 | Last run has 2 or more compactions (`compactions`)                        | Split                           |
| 7 | More than `split_steps` (150) steps without acceptance                    | Split                           |
| 8 | None of the above                                                         | No split, normal RESUME prompt  |

The runner-side error codes `child_hung` and `orphan` are not split triggers. They end the run
and are reported as failures, see `ahawr-runner/README.md`.

## Why the split did not start after a timeout (fixed 2026-11-15)

Seen on `ahawr-mission-dependencies-p4` T038 and `audio-dub-speed-p5` T053: the Worker ran the whole runner limit
(error code `timeout`) and the mission ended `failed` with no split. Two causes:

1. The workflow does not loop after a permanent Worker failure: it stores the failed state and ends. The split is decided
   only on the next launch of the task (`unfinished` in Build Worker Run Input: a session and a worker status other than
   `completed`). Prepare Workflow Failure Persistence read the run with `$(name).all()`, which returns the last execution
   of the node in this workflow run, so for the tenth task of an execution it stored the run, session and status
   (`completed`) of the task before. The next launch saw nothing unfinished and never looked the timeout up.
   `failure_state_task_match_patch.py` (in `launch_mission.sh`) keeps a Load Worker Result row or a Reviewer State item
   only when it belongs to the failing task. Test: `data-tables/ops/adaptive_split/failure_state_split.test.js`.
2. A failed mission is skipped by the queue unless it has `retry_failed`, so nothing relaunched it. `queue_board.py next`
   now relaunches a failed mission whose last Worker run ended with error code `timeout`, at most
   `MAX_TIMEOUT_RELAUNCHES` (2) times per mission and task (counter file `timeout_relaunch_counts.json` in the ops folder;
   a split part does not split again, so the bound stops a loop). Tests: `tests/test_queue_board.py`.

Not covered by an end-to-end run: the runner limit is one environment setting (`CLAUDE_RUNNER_MAX_RUN_SECONDS`), so a
live check with a 60 s limit needs the runner recreated with that value during a pause and restored afterwards.

## Flags and defaults

The settings live in `features_json` (the `features` entries of `hermes_config`, see
`ahawr-runner/docs/features-config.md`). A mission can override them with `mission_features_json`.
The defaults and the minimums of every feature are in one file,
`ahawr-runner/src/ahawr_runner/features_defaults.json`: `features_config.py`, the features route (the
`feature_defaults` and `param_minimums` fields) and `features_config_patch.py` all read that file, and none
of them keeps a copy. `features_config_patch.py` reads it at patch time when it writes the Constants node,
and flattens the resolved values into it; the split nodes read the flattened values, not the JSON. Every
feature defaults to off and the operator turns it on in `features_json`. The table below mirrors
`features_defaults.json`.

| Feature key (`features_json`)     | Default      | Flattened constant(s)                 | Effect when enabled                                              |
|-----------------------------------|--------------|---------------------------------------|------------------------------------------------------------------|
| `adaptive_split.enabled`          | false        | `adaptive_split`                      | Enables the split trigger and the split path                     |
| `adaptive_split.split_steps`      | 150          | `split_steps`                         | Step count that triggers a split                                 |
| `adaptive_split.max_splits`       | 6            | `max_splits`                          | Splits allowed per mission                                       |
| `adaptive_split.max_total_tasks`  | 40           | `max_total_tasks`                     | Hard ceiling on tasks after splits                               |
| `plan_no_truncate.enabled`        | false        | `plan_no_truncate`                    | Plan runs whole up to `max_tasks`, then split into parts         |
| `plan_no_truncate.max_tasks`      | 15           | `max_tasks`                           | Tasks per part, and the largest plan that runs whole             |
| `plan_no_truncate.max_tasks_hard` | 30           | `max_tasks_hard`                      | A part may be extended up to this many tasks so that a phase is not cut in the middle |
| `run_hygiene.enabled`             | false        | `run_hygiene`                         | Cancels the previous run of a session before a new one (also on when `adaptive_split` is on) |

A mission override of `adaptive_split.enabled` and `split_steps` alone, as the live proof uses, leaves the
other keys at their global values.

The runner's own settings (`CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS`, `CLAUDE_RUNNER_WATCHDOG_CPU_SECONDS`)
are separate and are documented in `ahawr-runner/README.md`.

## Operator commands

Run these only when the task names them. Nothing here is run automatically.

1. Apply the patch to a scratch copy of a fresh export. It never touches n8n:

   ```
   python data-tables/ops/adaptive_split_patch.py --src <export.json> --out data-tables/ops/adaptive_split/scratch/<name>.json
   ```

   The script refuses an `--out` equal to `--src` and any `--out` outside `adaptive_split/scratch/`.
   A second run on its own output changes nothing (idempotent).
2. Check the copy with the tests, then review the diff against the export.
3. Import the scratch copy into n8n during a pause: send `/pause <reason>` to the Telegram bot
   (`docs/telegram-bot.md`), wait until the Pause Gate blocks the next task, import the file in the n8n
   interface, then send `/pause off`. Do not import while a run is active.
4. Turn on the features in `features_json`: first `plan_no_truncate` and `run_hygiene`, then
   `adaptive_split`. Turn each on alone and watch one mission before the next. For a single mission, set
   the same keys in its `mission_features_json` instead (the live proof in `features-config.md` does this).
5. Rebuild the runner after a change to its code (parts 3 and 6 of the change touch `ahawr-runner`):

   ```
   cd ahawr-runner && docker compose up -d --build
   docker compose exec ahawr-runner curl -s http://127.0.0.1:8700/health
   ```

   Rebuilding restarts the runner container. Do it only with the queue paused.
6. Read-only check for orphan processes left by earlier runs in the runner container:

   ```
   docker exec ahawr-runner ps -eo pid,ppid,etimes,args | grep -E 'claude|node'
   ```

   A `claude` or `node` process whose parent is not the runner, or that runs long with no active run,
   is an orphan. The runner's watchdog reports it as `orphan`. Do not kill processes by hand; report them.

## Rollback

1. Set `adaptive_split.enabled`, `plan_no_truncate.enabled` and `run_hygiene.enabled` back to false in
   `features_json`, and clear or disable the same keys in any `mission_features_json`. Behaviour returns to
   the pre-change path at once, with no re-import. Do not re-run a mission that ran with the split.
2. The workflow patch is not undone by the flags. `adaptive_split_patch.py` and `telegram_progress_patch.py`
   have no reverse script. To remove them, pause the queue with `/pause`, import the pre-launch backup
   `wf_before_<mission>.json` (saved by `launch_mission.sh`) in the n8n interface, and resume. That backup
   also undoes every other patch of the same launch. The backup content has not been verified against a
   live export; check it before import.
3. Rebuild the runner only if its code must go back to the previous version.

## Node tests and the Python replica

The reference tests are node tests: `data-tables/ops/adaptive_split/plan_gate.test.js` and
`task_split.test.js`, run with `node --test` from the `adaptive_split/` directory. Each test
builds the patched scratch copy in-process with `adaptive_split_patch.py` (it never reads the
stale `scratch/_run1.json`) and loads the node jsCode from that copy. Where node is not installed,
`data-tables/ops/adaptive_split/plan_gate_replica.py` is a line-by-line Python port of `plan_gate.js`,
and `test_plan_gate.py` runs against the replica. The replica must be kept in step with the JS: a
change to `plan_gate.js` needs the same change in the replica and new cases in both test files.
The Python tests are `test_plan_gate.py`, `test_task_split.py` and `test_patch.py` (pytest).

## Not verified

No real n8n run and no real Worker, Reviewer or Architect run has been made with these changes.
The tests cover the pure functions and the patch script only.

## Plan gate retry and Parse Plan (2026-11-09)

On 2026-11-08 a 25-task plan was stored as its first 15 tasks: Evaluate Architect asked for a "too wide" retry (`architect_gate_retry`) that no node routes back to the Architect, so the plan went on without a `plan_decision`, Plan Gate returned nothing, no part mission was created, and Parse Plan cut the plan to `max_tasks`. `data-tables/ops/plan_gate_retry_patch.py` (applied by `launch_mission.sh`) sets `GATE_RETRY_ROUTED=false` in Evaluate Architect, so the gate always decides, and makes Parse Plan follow the decision when `plan_no_truncate` is on: a split keeps the tasks of part 1, a whole or refused plan keeps every task. With the flag off the old cut stays. Check: `node data-tables/ops/plan_gate_retry_check.js <patched workflow.json> <architect output>` replays both nodes on a real Architect output.
