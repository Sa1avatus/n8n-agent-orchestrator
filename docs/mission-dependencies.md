# Mission dependencies: the durable note

Status: the rule, the API fields and the dashboard part are in the runner; the workflow side is three
patch scripts in `data-tables/ops`. Nothing of it has been applied to the live workflow yet — see
"Operator steps". Naming follows `docs/naming.md`: mission, queue, position, flag, setting.

The queue lives in PostgreSQL (`board.queue`, not an n8n data table): one row per mission at one
position, with the optimistic version check and the changelog audit on every write. `depends_on` is a
mission-level dependency: it names the missions that must finish before this one runs. Task-level
dependencies (`board_order.py`) are a different thing and are not touched here.

## Data

| Where | What |
|---|---|
| `ahawr-runner/src/ahawr_runner/board_pg.py:97` (`_DDL`) | `board.queue` has `depends_on jsonb NOT NULL DEFAULT '[]'`: a list of `mission_id` strings stored with the queue entry |
| `board_pg.py:421` `_ensure_queue_columns`, called at `:415` | the additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS depends_on jsonb NOT NULL DEFAULT '[]'` runs when the store is constructed; no hand-run SQL, no n8n data-table column |
| `board_pg.py:614` `list_dependencies(mission_id)` | the stored ids, `[]` when the entry has none |
| `board_pg.py:1084` `set_dependencies(mission_id, depends_on, expected_version)` | the write: queue lock, version check, changelog row, the same order as `set_queue` |
| `board_api.py:376-378` | answers carry `depends_on`, `dependency_state`, `dependency_reason` — only when the flag is on |

## The rule: due, waiting, blocked

One module holds the rule: `ahawr-runner/src/ahawr_runner/board_dependencies.py`, pure (no DB, no I/O).
The runner API and the shell queue both use it; `data-tables/ops/queue_board.py` never imports the
runner package and reads the module by path (`DEPENDENCY_RULE_PATH`, `queue_board.py:83`). There is no
JS twin: the browser renders what the API answers, so it cannot disagree with the queue.

| State | When | Effect |
|---|---|---|
| due | no `depends_on`, or every dependency `completed` | the entry may run |
| waiting | a dependency is not completed — still queued, removed from the queue, or an unknown id | skipped, never removed |
| blocked | a dependency `failed` | reported on stderr, never picked |

- `classify_queue(entries, state_by_id)` (`board_dependencies.py:165`) returns `{state, reason, depends_on}`
  for every entry; the reason text is what the UI and the guard show, the state is the stored verdict.
- `find_first_due(entries, state_by_id)` (`:376`) picks the active mission wherever its position is, then
  the first due entry in position order; a completed entry is skipped.
- A dependency id that is neither in the queue nor completed counts as unmet: the mission waits.

Validation before a write, `validate_dependencies` (`:134`):
- an unknown id → 400 refusal naming it (`dep.missing`);
- a cycle (`a -> b -> a`) → 422 refusal naming the closing pair (`dep.cycle`);
- `build_dependencies` (`:58`) normalizes the raw value: blanks, duplicates and a self-reference are
  dropped, the kept first bad entry lets the message name it.

Moves and drops, decided by the same module:
- `find_move_problems(entries, target_position, moving_id, move_dependents, state_by_id)` (`:323`)
  refuses a drag, "to the top", "to the end" or a kanban drop that would leave a mission before one it
  waits for;
- `find_move_blockers` (`:357`) names the ids the refusal is blocked by;
- `plan_move_order` (`:275`) lays out the carried dependents right after the moved mission.

## UI: drag-and-drop and "run anyway"

`ahawr-runner/src/ahawr_runner/board_mission_dependencies.js` is render-only. It reads
`dependency_state` / `dependency_reason` from the API answer and never recomputes them; with the flag
off the answer has no `depends_on` key and every renderer returns `""`.

- Queue card and kanban card: the `after: <ids>` line and the waiting/blocked chip
  (`board_queue.js:120`, `board_view_kanban.js:74`), CSS `q-deps`, `dep-chip`, `q-dep-waiting`,
  `q-dep-blocked`.
- Drag and drop: the card move posts `/api/board/queue/move` (`board_dnd.js:85`, `:141` for "to the
  top"); a dependency refusal is handled by `handleDependencyRefusal` (`board_actions.js:243`), which
  names the ids and offers "Move them too" (`move_dependents: true`, `board_actions.js:263`) or keeps
  only the dragged mission.
- "Run anyway": `POST /api/board/missions/{mission_id}/start` refuses a mission whose dependencies are
  not completed unless the body carries `run_anyway: true` (`board_api.py:1684`); a refused confirm
  sends nothing.
- The editor field "After missions" (`board_actions.js:453`, `board_mission.js:201`) is saved through
  `set_dependencies` with the version check and the existing diff-and-confirm step.
- Texts (the parity test checks all three copies): `q.dep`, `q.dep.none`, `q.dep.waiting`,
  `q.dep.blocked`, `q.dep.waiting.title`, `q.dep.blocked.title`, `m.dep`, `m.dep.hint`, `dep.missing`,
  `dep.cycle`, `dep.order`, `dep.start`, `dep.group`, `dep.anyway`, `btn.dep.group`, `btn.dep.only`,
  `btn.dep.anyway` — in `board_i18n_en.json`, `board_i18n_ru.json` and `BUNDLED_EN` of `board_i18n.js`.

## Split parts

Part N (N >= 2) of a split waits for the part before it: part 2 waits for the original mission, part 3
for `<mission_id>-p2`. Part 1 has no previous part, so its list is empty; missions split before the
patch keep an empty list and behave as before. Patch: `data-tables/ops/split_dependencies_patch.py`
(anchor `plan: partPlan(part, k),` in `Create Part Missions`).

## Architect block and its limits

- `build_dependency_context(mission, entries, state_by_id)` (`board_dependencies.py:462`) builds the
  "Missions this one depends on" text: id, name, state, then that dependency's plan summary or final
  report. Limits: each dependency text cut to `DEPENDENCY_REPORT_MAX_CHARS = 700`, the whole block to
  `ARCHITECT_BLOCK_MAX_CHARS = 4000` (`board_dependencies.py:21, 23`); a cut ends with `...`
  (`TRUNCATION_MARK`). The sentence the Architect must follow is `ARCHITECT_DEPENDENCY_RULE` (`:25`).
- It is served as the additive `dependency_context` field of `GET /api/board/missions/{mission_id}`
  (the helper `board_api.py:438-469`, added to the answer at `:1191`), only with the flag on and
  only when the mission has dependencies.
- `data-tables/ops/architect_prompt_dependency_block.py` adds one fetch of that route into
  `Build Architect Run Input` (`DEPENDENCY_FETCH_TIMEOUT_MS = 30000`) and appends the block when the
  answer carries it. The same patch adds `SPLIT_ORDER_RULE` to the prompt, and only when the flag is
  on: parts ordered so that a part uses only its own earlier tasks or the output of an earlier part,
  and part N stated as waiting for part N-1.
- Flag off, no `depends_on`, or the board unreachable: the Architect prompt is byte for byte the one
  from before the patch.

## Plan lint

`data-tables/ops/plan_dependency_lint_patch.py` runs inside `Evaluate Architect` and mirrors
`find_part_order_problems` (`board_dependencies.py:400`): a task of part K may name (in its
dependencies or its scope) only its own earlier tasks or what an earlier part produced. The JS mirror
in the node and the Python function are tested against each other
(`ahawr-runner/tests/test_architect_dependencies.py`). A plan with problems goes back to the Architect
once — `LINT_RETRY_LIMIT = 1`, bounded by `gateAlreadyRetried` — and the second failure follows the
existing plan-gate path: the plan is accepted with a note. The patch needs `plan_gate_retry_patch.py`
applied first and exits with a message naming a missing anchor. With the flag off `GATE_RETRY_ROUTED`
stays false and the plan path is the one from before the patch.

## Flag `mission_dependencies`

`ahawr-runner/src/ahawr_runner/features_defaults.json:29-30` — `"mission_dependencies": {"enabled": false}`, read by
`_feature_enabled` in `board_api.py`. Off means: no `depends_on`, `dependency_state` or
`dependency_reason` keys in any answer (absent, not null), no guard on moves, drops or starts, no
Architect block, no plan lint — the behaviour is byte for byte the old one. Turn it on in
`features_json`, or for one mission in its `mission_features_json`.

## Operator steps

Nothing here has been applied live: the three patch files exist in `data-tables/ops` and
`launch_mission.sh` does not run them yet. One window, queue paused (`/pause <reason>`,
`docs/telegram-bot.md`):

1. Store migration — nothing to run by hand: `_ensure_queue_columns` (`board_pg.py:421`) adds the
   column with its default when the board store is constructed. The step is the runner rebuild in the
   window, requested through `POST /v1/host-actions` on the runner.
2. Export a fresh copy of the live workflow into `data-tables/ops`. Not `AHAWR_Workflow.json` — that
   file is not what runs.
3. Patch the copy, in this order. Each is idempotent (the second run prints `already patched`) and
   asserts every anchor exactly once:

   ```
   PYTHONIOENCODING=utf-8 python data-tables/ops/split_dependencies_patch.py   <copy.json>
   PYTHONIOENCODING=utf-8 python data-tables/ops/architect_prompt_dependency_block.py <copy.json>
   PYTHONIOENCODING=utf-8 python data-tables/ops/plan_dependency_lint_patch.py  <copy.json>
   ```

   `split_dependencies_patch.py` follows the already-applied `adaptive_split_patch.py`;
   `plan_dependency_lint_patch.py` needs `plan_gate_retry_patch.py`. Check the copy:
   `node data-tables/ops/js_syntax_check.js <copy.json>` → `bad 0`.
4. Import the patched copy in the n8n interface while the queue is paused, then `/pause off`.
5. Turn the flag on: `mission_dependencies.enabled` true in `features_json`
   (`ahawr-runner/docs/features-config.md`), or in one mission's `mission_features_json`. Watch one
   mission before the others.

Rollback: set the flag back to false — the guard, the Architect block and the lint stop at once, no
re-import. The patches are not undone by the flag; undoing them means importing the pre-launch backup
`wf_before_<mission>.json` saved by `launch_mission.sh`.

## Tests

`ahawr-runner/tests/test_board_dependencies.py` (rule, validation, moves),
`test_mission_dependencies.py` (answers and the flag-off contract), `test_split_dependencies.py`,
`test_architect_dependencies.py` (the block and the lint mirror),
`ahawr-runner/tests/board_mission_dependencies.test.js` (rendering), `test_queue_board.py` (the shell
pick of `find_first_due`).
