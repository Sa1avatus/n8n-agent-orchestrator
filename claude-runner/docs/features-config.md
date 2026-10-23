# Features config (features_json)

`features_json` switches the workflow features on or off and sets their parameters. It replaces the separate
flag columns for the adaptive split (`adaptive_split`, `plan_no_truncate`, `run_hygiene`) with one JSON setting,
the same way `roles_json` replaces the model columns (see `roles-config.md`).

Status: design. The pure core (`board_features.js`, `features_config.py`), the patches and the board section are
written by the features-config mission; until then no reader uses this document's rules.

## Shape of features_json

`features_json` is a JSON object. Each key is a feature name. Each feature is an object with `enabled` (a boolean)
and the feature's own parameters:

```json
{"plan_no_truncate": {"enabled": true, "max_tasks": 15, "max_tasks_hard": 30},
 "adaptive_split": {"enabled": false, "split_steps": 150, "max_splits": 6, "max_total_tasks": 40},
 "run_hygiene": {"enabled": true}}
```

The example is the seed value in `data-tables/ops/_make_features_config_mission.py`, chosen by the operator; it is not the default.

- An empty value, `{}` or an absent column means every feature is off and every parameter has its default.
- A feature that is not in the object is off. A parameter that is not in a feature's object takes its default.
- Keys are `snake_case`, as the naming rules in `docs/naming.md` require.

## Defaults

| Feature | Key | Default | Minimum | Flattened constant |
|---|---|---|---|---|
| `plan_no_truncate` | `enabled` | off (0) | 0 | `plan_no_truncate` |
| `plan_no_truncate` | `max_tasks` | 15 | 1 | `max_tasks` |
| `plan_no_truncate` | `max_tasks_hard` | 30 | 1 | `max_tasks_hard` |
| `adaptive_split` | `enabled` | off (0) | 0 | `adaptive_split` |
| `adaptive_split` | `split_steps` | 150 | 1 | `split_steps` |
| `adaptive_split` | `max_splits` | 6 | 0 | `max_splits` |
| `adaptive_split` | `max_total_tasks` | 40 | 1 | `max_total_tasks` |
| `run_hygiene` | `enabled` | off (0) | 0 | `run_hygiene` |

`run_hygiene` has no parameters. The minimums are the ones the adaptive split code reads
(`optNum(row.<flag>, <default>, <minimum>)` in the design notes, `data-tables/ops/adaptive_split/NOTES.md`).
`max_tasks` has the default 15 only when the legacy `max_tasks` column is absent (see the flatten section).

## Validation

The parser (`parseFeatures(text, mission_text)` in `board_features.js`, mirrored in `features_config.py`) returns
the features and a list of errors. A value with any error is rejected; nothing is stored.

- Unknown feature names and unknown parameter names are errors.
- `enabled` must be a boolean.
- Numbers must be integers at or above their minimum (table above).
- `max_tasks_hard` must be at least `max_tasks`.
- The text must parse as JSON. An error names the feature and the key, so the operator can fix it.

The board validates `features_json` and `mission_features_json` on save with the same code, as it does for
`roles_json`. Both implementations pass one shared cases file, `claude-runner/tests/features_cases.json`.

## Per-mission override (mission_features_json)

A mission can override the global features with `mission_features_json`, a column of the missions table. It is
optional: a reader that finds the column absent treats the override as empty and uses the global value.

Merge rule: the override is applied per feature. A parameter set in the mission's feature object replaces the
global value of that parameter; parameters the mission does not set come from the global value, then from the
default. A feature the mission does not mention is the global feature unchanged.

This is the intended rule. `board_features.js` is the reference once it is written; if its behaviour differs,
this section is changed to match it.

## Flatten to the legacy constants

Every node keeps reading `constants.<flag>` as before. `flatten(features)` in `board_features.js` produces
these constants:

| Constant | Source in features_json |
|---|---|
| `plan_no_truncate` | `plan_no_truncate.enabled`, 0 or 1 |
| `max_tasks` | `plan_no_truncate.max_tasks` |
| `max_tasks_hard` | `plan_no_truncate.max_tasks_hard` |
| `adaptive_split` | `adaptive_split.enabled`, 0 or 1 |
| `split_steps` | `adaptive_split.split_steps` |
| `max_splits` | `adaptive_split.max_splits` |
| `max_total_tasks` | `adaptive_split.max_total_tasks` |
| `run_hygiene` | `run_hygiene.enabled`, 0 or 1 |

Legacy columns are the fallback. If a parameter is absent from `features_json` and its legacy column exists, the
column value is used; otherwise the default is used. `docs/config-columns-plan.md` records the `max_tasks` column
as 30 and the `features_json` default as 15. `claude-runner/docs/board-data-model.md` (line 159) records 20 for
the same profile row. These three values disagree and must be settled before the golden test is written. With
`features_json` empty and the column present, `max_tasks` flattens to the column value. The default 15 applies only
when the column is absent. The three flags and the adaptive parameters have no legacy column, so their fallback is
the default.

Golden test: an empty `features_json` must flatten to constants byte-for-byte equal to today's.

## Patch order

The live workflow is exported at launch and patched by the ops patches of `data-tables/ops/launch_mission.sh.new`
(lines 33-45) in this order:

| # | Patch (line) |
|---|---|
| 1 | `notice_split_patch.py` (33) |
| 2 | `telegram_model_patch.py` (34) |
| 3 | `telegram_mission_patch.py` (35) |
| 4 | `telegram_gate_patch.py` (36) |
| 5 | `blind_guard_patch.py` (37) |
| 6 | `think_config_patch.py` (38) |
| 7 | `roles_config_patch.py` (39) |
| 8 | `features_config_patch.py` (40) |
| 9 | `adaptive_split_patch.py` (41), written to scratch, then moved back over `wf_next.json` (42) |
| 10 | `retry_patch.py` (43) |
| 11 | `telegram_progress_patch.py` (44) |
| 12 | `qa_workflow_patch.py` (45) |

`ensure_live_state_row.py` runs after the patches (line 47); it is not one of the numbered patches.

### Why no patch undoes another

- 1 rewrites the whole TASK STARTED notice (the `Split Task Notice` node) and 2 adds the model line to the
  Telegram texts that follow, so 1 runs before 2.
- 3 anchors the mission line to the texts 1 and 2 create (`📌 <mission>` in `Split Task Notice`, `Send a text
  message5/6/7` and `Restore Review Notification Context`).
- 4 fixes only the chat-id condition of the five gate If nodes (`item` -> `first()`); 5 anchors into the retry
  node; both leave the Telegram texts untouched.
- 6 and 7 edit Constants and the Build nodes by anchor. 8 adds the flattened `features_json` constants to the
  same Constants node, so it runs after them and keeps their anchors (the Constants tail and `mission_roles_json`)
  unchanged.
- 9 makes anchored edits to the split nodes. Its anchors must match the output of 1-8, and its
  `--out`/`mv` pair (lines 41-42) leaves every other node byte-identical, so it undoes nothing 1-8 wrote.
- 10 (retry) runs after 9 because it covers the Telegram nodes that 9 creates: the line-43 comment says
  "retries for Telegram nodes, including the ones created by adaptive_split".
- 11 (telegram_progress) runs after 9: it adds the position wording (part fields) to the TASK STARTED and
  REVIEW notices, and the part fields are written by the `Create Part Missions` node that 9 adds. It raises
  if that node is missing while the workflow carries the adaptive-split flag, so it must run after 9.
- 12 adds the QA nodes and prompt changes for the `qa` mode of the global `roles_json`; its anchors do not
  conflict with the preceding edits.
- Every patch asserts each anchor exactly once, is idempotent by its marker, and writes nothing when an anchor is
  missing. A test applies all ops patches in launch order to a copy of a fresh live export, twice, and checks that
  the result is stable and every marker is present.

## Operator activation

Nothing here is run by the mission. The operator does this:

1. Stop the queue before a launch that applies the patches.
2. Add the new patches to the launch order in `data-tables/ops/launch_mission.sh` in the order above. This needs
   the operator's review; the design does not change that script.
3. Add the `mission_features_json` column to the missions table in the n8n interface, only if per-mission
   overrides are wanted. Readers tolerate its absence. Adding a column is a schema change and needs the operator's
   approval; columns are never added with raw SQL.
4. Check the `features_json` value of the `claude-code` profile (`hermes_config`, id 5) against the intended
   features. The mission records a value there already; it is not reset by this design.
5. Resume the queue. The launch exports the live workflow and applies the patches in the order above.

## Rollback

1. Set the features back to off: `enabled` false in `features_json` (and in `mission_features_json` where set).
   Behaviour returns to the pre-change path at once, with no re-import.
2. If the workflow itself must go back, stop the queue and import the backup
   `data-tables/ops/wf_before_ahawr-features-config.json` in the n8n interface. Its content has not been checked
   against a live export; check it before import.
3. Remove the new patch lines from `data-tables/ops/launch_mission.sh` so that the next launch does not apply them.

## What is not changed

- The live workflow and `AHAWR_v13_ClaudeCode.json` are not edited by the design. The patches are files, applied at
  launch.
- The design does not edit `data-tables/ops/launch_mission.sh` itself; that change is listed under Operator activation.
- The legacy `max_tasks` column is kept until the removal step of `docs/config-columns-plan.md`. No column is
  removed by this design; `mission_features_json` is the only column it may add, and only with approval.
- The roles, thinking, retrieval and engine settings keep their columns and their readers.
- The runner's own settings (`CLAUDE_RUNNER_WATCHDOG_INTERVAL_SECONDS`, `CLAUDE_RUNNER_WATCHDOG_CPU_SECONDS`) are
  separate and are documented in `claude-runner/README.md`.
- No new runtime dependency and no version bump are part of this document.

## Live proof

Operator only: it uses the queue and paid models, so the mission that wrote this section did not run it.
The steps follow `data-tables/ops/_make_split_trial_mission.py` and `data-tables/ops/launch_mission.sh.new`.
The trial is one throwaway mission, `ahawr-split-trial`, with four tasks: T001, T002 (the wide task), T003
and T004. Only this mission gets adaptive split; the global `features_json` stays as the operator set it.

1. Check the prerequisites.
   - The missions table has the `mission_features_json` column. Readers treat an absent column as no override,
     so without it the trial runs with no split and nothing reports the difference. Adding the column is a
     schema change made in the n8n interface (Operator activation, step 3).
   - `data-tables/ops/launch_mission.sh` has the same patch lines as `launch_mission.sh.new`, including
     `features_config_patch.py`, `adaptive_split_patch.py` and `telegram_progress_patch.py`. Compare them with
     `diff`. If they differ, the trial would run without the patches; the operator adopts the new script first.
2. Generate the trial (it writes only to the scratch folder):

   ```
   cd data-tables/ops && python _make_split_trial_mission.py
   ```

   It writes `_scratch/split_trial/mission_ahawr-split-trial.csv`, `expected_outcomes.json` and `pkg/`. The
   script stops with `mission_features_json is invalid` when the override does not validate. The override is
   `{"adaptive_split":{"enabled":true,"split_steps":25}}`, set for this mission only.
3. Put the mission row in the missions table with its `mission_features_json`, as for any other mission. The
   CSV is not in `data-tables/missions/`, and the route from the CSV to the table is not verified here; check it
   in the n8n interface before the launch.
4. Pause, then apply the patches and start the mission.
   - Send `/pause <reason>` to the Telegram bot (`docs/telegram-bot.md`) and wait until no AHAWR execution is
     running. The launch refuses to start while one runs.
   - Run `bash data-tables/ops/launch_mission.sh ahawr-split-trial`. The script saves the live export as
     `data-tables/ops/wf_before_ahawr-split-trial.json` (the rollback backup), sets Get Mission to the trial,
     applies the patches in the order of `launch_mission.sh.new`, checks the Telegram expressions, imports the
     workflow and starts the run. Expect the line `started ahawr-split-trial`.
   - If the pause still blocks the first task, send `/pause off` after the start line. Whether it does is not
     verified.
5. Watch the run. The expected results are in `_scratch/split_trial/expected_outcomes.json`. Record what you see.
   - Only T002 is split. The parts get letter-suffixed ids (T002A, T002B, and maybe more). T001, T003 and T004
     keep their ids, and no id is reused or renumbered.
   - The TASK STARTED notice shows the position: `Task N of M (ID): Title` for a plain task, and
     `Task N of M, part P of C, overall X of Y (ID): Title` when the task is a part of a split plan. Which form a
     split task shows is not verified in a run.
   - The split notice appears in Telegram once per split. Its wording is not in `telegram_progress_patch.py` and
     was not checked for this section.
   - Splits stay at or below 6 (`max_splits`) and total tasks at or below 40 (`max_total_tasks`).
   - The run ends with `python -m pytest tests/test_pkg.py -q` passing in the working folder. Keep its last line.
   - The runner event names for a split are not confirmed in the code read for this section. Read the split
     events in the dashboard and record the names there.
6. Roll back.
   - Clear `mission_features_json` of `ahawr-split-trial`, or set `adaptive_split.enabled` to false. Do not
     re-run the mission. Other missions are unchanged.
   - To remove the patches of this launch from the workflow, pause (`/pause`), import
     `data-tables/ops/wf_before_ahawr-split-trial.json` in the n8n interface and resume. The backup is the export
     taken before this launch's patches, so it removes only this launch's patches. It has not been checked against
     a live export; check it before the import.

## Telegram texts

The notices are changed by `data-tables/ops/telegram_progress_patch.py` (launch line 44 of
`launch_mission.sh.new`) and TASK STARTED also by `notice_split_patch.py` (several messages of at most 4096
characters). The old texts below are quoted from `AHAWR_v13_ClaudeCode.json`, which is not the workflow that
runs; the live export was not read for this table. "Plain" is a mission without a part count.

| Message | Old text | New text, plain mission | New text, part of a split plan |
|---|---|---|---|
| TASK STARTED (`Split Task Notice`) | `▶️ TASK STARTED` / `🤖 Worker: <model>` / `T001 (out of 5): Title` | `Task 1 of 5 (T001): Title` | `Task 5 of 15, part 2 of 3, overall 20 of 40 (T012): Title`; a split part adds `, a part of the split of T010` inside the brackets |
| REVIEW (`Restore Review Notification Context`) | `🧪 REVIEW T001` (id only) | `🧪 REVIEW Task 1 of 5 (T001)` | `🧪 REVIEW Task 5 of 15, part 2 of 3, overall 20 of 40 (T012)` |
| PLAN RECEIVED (`Send a text message5`) | `📋 PLAN RECEIVED` / `🤖 Planner: <model>` / plan summary | unchanged | adds the line `Tasks: 15, part 2 of 3, overall 40 tasks` before the summary |
| WORKFLOW FAILED (`Send a text message6`) | `Task: T012` | unchanged | `Task: T012 (Task 5 of 15, part 2 of 3, overall 20 of 40)` |
| WORKFLOW APPROVED (`Send a text message7`) | `Completed tasks: 4/5` (old count, see below) | `Completed tasks: 5/5` | `Completed tasks: 15/15, part 2 of 3, overall 30 of 40` |

Changes that apply to plain missions too, which the module docstring of `telegram_progress_patch.py` does not say:

- TASK STARTED and REVIEW always show the position, so a plain mission's text changes there.
- The approved count is `task_index + 1` in `Approved Result` for every mission. The old value was `task_index`,
  so a plan of five tasks that finished showed `4/5`; it now shows `5/5`.

The new texts are checked by `data-tables/ops/test_telegram_progress_patch.py`. Its expectations include
`Task 20 of 20 (T017): Title`, `Task 5 of 15, part 2 of 3, overall 20 of 40 (T012): Title`,
`Tasks: 15, part 2 of 3, overall 40 tasks` and `Completed tasks: 15/15, part 2 of 3, overall 30 of 40`.

To undo the progress patch: remove line 44 of `launch_mission.sh.new` (and of the live `launch_mission.sh`) so
that the next launch does not apply it. There is no reverse script. A patch already imported is undone only by
importing the pre-launch backup `wf_before_<mission>.json` at a pause; that backup also undoes every other patch
of the same launch.
