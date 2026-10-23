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
   The runner side of this is the supersede and watchdog behaviour in `claude-runner/README.md`.

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
and are reported as failures, see `claude-runner/README.md`.

## Flags and defaults

The settings live in `features_json` (the `features` entries of `hermes_config`, see
`claude-runner/docs/features-config.md`). A mission can override them with `mission_features_json`.
`features_config_patch.py` flattens them into the Constants node of the workflow; the split nodes read
the flattened values, not the JSON. Every feature defaults to off.

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
are separate and are documented in `claude-runner/README.md`.

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
5. Rebuild the runner after a change to its code (parts 3 and 6 of the change touch `claude-runner`):

   ```
   cd claude-runner && docker compose up -d --build
   docker compose exec claude-runner curl -s http://127.0.0.1:8700/health
   ```

   Rebuilding restarts the runner container. Do it only with the queue paused.
6. Read-only check for orphan processes left by earlier runs in the runner container:

   ```
   docker exec claude-runner ps -eo pid,ppid,etimes,args | grep -E 'claude|node'
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
