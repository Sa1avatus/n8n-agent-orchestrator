# Mission authoring: how to size a mission and its tasks

A mission is one objective for the Architect. It is planned into tasks, and each task runs as a Worker
run with a Reviewer check. Small, single-area missions finish cleanly; large ones time out, get
compacted and are split by the adaptive split path (`docs/adaptive-split.md`). Write missions so the
split path is rarely needed.

## Rules

1. **12-15 tasks per plan.** Aim for 12-15 tasks. The plan gate runs a plan of up to `max_tasks` (15)
   whole; above that the plan is split into parts. Fewer than 12 usually means the tasks are too wide.
2. **One area per mission.** A mission touches one area of the code (for example the board UI, or the
   runner's queue). Two areas are two missions.
3. **Parts as separate missions.** If the work has `PART` sections, each part is its own mission with
   its own id, not one long objective. A part is finished and reviewed before the next one is queued.
4. **Tests, docs and report are separate tasks.** Write the implementation, the tests, the docs and the
   report as separate tasks, each with its own acceptance check. Do not hide a test inside an
   implementation task: the Reviewer cannot tell whether it ran.
5. **Lint before import.** `data-tables/ops/mission_lint.py <mission_id>` checks the mission row. It warns
   when the objective
   - has more than 5 `PART`/`ЧАСТЬ` sections;
   - is longer than 9000 characters;
   - names more than 4 code areas;
   - asks for more than 1 final report.
   Its advice is "split into several missions of at most 12-15 tasks, one area each". The same run also
   checks before launch: every path named as `operator copy at <path>` exists on the host, the
   `working_directory` exists, no task id in the objective carries a letter suffix (`T001A`, the id the
   Architect gives a task after a split), and every acceptance criterion is non-empty and names a command
   or a test path it can run. With `--providers <json>` (default `data-tables/ops/providers_snapshot.json`)
   the roles chain in the mission row is checked against that snapshot: a model not in the stored providers
   list is a `FAIL`, and when the snapshot is absent or unreadable the check prints `NOT RUN` with the
   reason instead of failing. One `PASS`/`FAIL`/`NOT RUN` line per check, one `WARN` line per size rule; any
   `FAIL` exits 1, `--strict` also exits 1 on warnings. The script writes nothing and never calls a provider.
6. **Be concrete in each task.** Name the files in scope (at most 3 per task), state the acceptance
   check, and state what is out of scope. A task with "whole", "entire" or "completely" near a module
   name is flagged by the plan gate.
7. **Report in one place.** One report request per mission, written at the end. It lists what changed,
   the checks that ran, and what did not.

## Examples from this repository

- `data-tables/ops/_make_board_mission.py` (mission `ahawr-dashboard-mission-board`): a single-area
  feature mission (the dashboard mission board). Its name covers one area, so it is the model for a
  mission that stays inside one area. Check its objective with `mission_lint.py` before you reuse it.
- `data-tables/ops/_make_improvements_mission.py` (mission `ahawr-improvements-v1`): five independent
  improvements in one objective. This is the pattern to avoid: MCP search, board tools, Reviewer
  calibration, the stuck detector and secrets hygiene are different areas. Split it into one mission
  per area, as the adaptive split mission did for its own parts (`data-tables/ops/_make_split_mission.py`).
- `data-tables/ops/_make_split_mission.py` (adaptive split): six parts, each a separate area and
  mission. Part 5 is the patch and tests, part 6 is the runner's stale-run handling. It is the model
  for the parts rule above.

Task counts: `ahawr-dashboard-mission-board` has 30 tasks (CHANGELOG, 2026-10-17 entry).
The two mission scripts (`_make_board_mission.py`, `_make_improvements_mission.py`) store only
the objective, rules and acceptance criteria — the task list and ids are created by the Architect
at plan time and live in `plan_json`, not in the script. For `ahawr-improvements-v1` the task
count is therefore not fixed by the script; count it from the plan (`plan_json` of the mission's
Live State row, or the dashboard's task cards) before you cite a number.

## Checklist before queueing

- [ ] 12-15 tasks, or fewer with a reason
- [ ] one code area
- [ ] each part is its own mission
- [ ] tests, docs and report are their own tasks
- [ ] `mission_lint.py` prints no FAIL line and no WARN line
- [ ] every task names at most 3 scope files and states its acceptance check
