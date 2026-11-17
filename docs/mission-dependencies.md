# Mission dependencies: the durable note

The queue lives in PostgreSQL (`board.queue`, not an n8n data table): one row per mission at one
position, version-checked and changelog-audited on every write. `depends_on` names the missions that
must finish before this one runs; task-level dependencies (`board_order.py`) are a separate thing.

## Data

| Where | What |
|---|---|
| `ahawr-runner/src/ahawr_runner/board_pg.py:97` (`_DDL`) | `board.queue` has `depends_on jsonb NOT NULL DEFAULT '[]'`: a list of `mission_id` strings stored with the queue entry |
| `board_pg.py:422` `_ensure_queue_columns`, called at `:416` | the additive `ALTER TABLE ... ADD COLUMN IF NOT EXISTS depends_on jsonb NOT NULL DEFAULT '[]'` runs when the store is constructed; no hand-run SQL, no n8n data-table column |
| `board_pg.py:625` `list_dependencies(mission_id)` | the stored ids, `[]` when the entry has none |
| `board_pg.py:1107` `set_dependencies(mission_id, depends_on, expected_version)` | the write: queue lock, version check, changelog row, the same order as `set_queue` |
| `board_api.py:430-432` | answers carry `depends_on`, `dependency_state`, `dependency_reason` — only when the flag is on |

## The rule: due, waiting, blocked

One module holds the rule: `ahawr-runner/src/ahawr_runner/board_dependencies.py`, pure (no DB, no I/O). The runner API and the shell queue both use it; `data-tables/ops/queue_board.py` reads it by path (`DEPENDENCY_RULE_PATH`, `queue_board.py:89`), never importing the runner package. The browser renders what the API answers, so it cannot disagree with the queue.

| State | When | Effect |
|---|---|---|
| due | no `depends_on`, or every dependency `completed` | the entry may run |
| waiting | a dependency is not completed — still queued, removed from the queue, or an unknown id | skipped, never removed |
| blocked | a dependency `failed` | reported on stderr, never picked |

- `classify_queue(entries, state_by_id)` (`board_dependencies.py:165`) returns `{state, reason,
  depends_on}` for every entry; the reason text is what the UI and the guard show, the state is the
  stored verdict.
- `find_first_due(entries, state_by_id)` (`:376`): an `active` mission resumes first wherever its
  position is, then the first due entry in position order; a completed entry is skipped.
- A dependency id that is neither in the queue nor completed counts as unmet: the mission waits.

Validation before a write, `validate_dependencies` (`:134`): an unknown id → 400 refusal naming it (`dep.missing`); a cycle (`a -> b -> a`) → 422 refusal naming the closing pair (`dep.cycle`). `build_dependencies` (`:58`) drops blanks, duplicates and self-references, keeping the first bad entry to name it in the message.

Moves and drops: `find_move_problems(entries, target_position, moving_id, move_dependents, state_by_id)` (`:323`) refuses a drag, "to the top", "to the end" or a kanban drop that would leave a mission before one it waits for; `find_move_blockers` (`:357`) names the blocking ids; `plan_move_order` (`:275`) lays the carried dependents right after the moved mission.

## Adaptive split

Part N (N >= 2) of a split waits for part N-1: part 2 waits for the original mission, part 3 for `<mission_id>-p2`; part 1 has no previous part, so its list is empty. Patch `data-tables/ops/split_dependencies_patch.py` (anchor `plan: partPlan(part, k),` in `Create Part Missions`) sits on top of the already-applied `data-tables/ops/adaptive_split_patch.py`; missions split before it keep an empty list.

## Dashboard

`ahawr-runner/src/ahawr_runner/board_mission_dependencies.js` is render-only, reading
`dependency_state` / `dependency_reason` from the API answer; with the flag off the answer has no
`depends_on` key and every renderer returns `""`.

- Queue card and kanban card: the `after: <ids>` line and the waiting/blocked chip
  (`board_queue.js:120`, `board_view_kanban.js:67`), CSS `q-deps`, `dep-chip`, `q-dep-waiting`, `q-dep-blocked`.
- Drag and drop: the move posts `/api/board/queue/move` (`board_dnd.js:85`, `:141` for "to the
  top"); `handleDependencyRefusal` (`board_actions.js:290`) names the blocking ids and offers "Move
  them too" (`move_dependents: true`, `board_actions.js:310`) or only the dragged mission.
- "Run anyway": `POST /api/board/missions/{mission_id}/start` (`board_api.py:1758`) refuses a
  waiting or blocked mission unless the body carries `run_anyway: true` (`board_api.py:1769`); a
  refused confirm sends nothing.
- The editor field "After missions" (`m.dep`, `board_mission.js:201`) saves through `set_dependencies`
  with the version check and the existing diff-and-confirm step.
- Texts go through `L()` (`dep.missing`, `dep.cycle`, `dep.order`, `dep.start`, `dep.group`, `dep.anyway`, `btn.dep.group`, `btn.dep.only`, `btn.dep.anyway`) in `board_i18n_en.json`, `board_i18n_ru.json` and `BUNDLED_EN` of `board_i18n.js`; a parity test checks the copies.

## Architect block and its limits

- `build_dependency_context(mission, entries, state_by_id)` (`board_dependencies.py:462`) builds the
  "Missions this one depends on" block (`DEPENDENCY_BLOCK_HEADING`, `:19`): id, name, state, then the
  dependency's plan summary or final report. Named limits: each dependency text cut to
  `DEPENDENCY_REPORT_MAX_CHARS = 700`, the whole block to `ARCHITECT_BLOCK_MAX_CHARS = 4000`
  (`board_dependencies.py:21, 23`), a cut ending in `...` (`TRUNCATION_MARK`, `:35`; the rule is
  `ARCHITECT_DEPENDENCY_RULE`, `:25`).
- Served as the additive `dependency_context` field of `GET /api/board/missions/{mission_id}`
  (`board_api.py:1245-1247`), only with the flag on and only when the mission has dependencies.
- `data-tables/ops/architect_prompt_dependency_block.py` adds one fetch of that route into
  `Build Architect Run Input` (`DEPENDENCY_FETCH_TIMEOUT_MS = 30000`) and appends the block when the
  answer carries it. The same patch adds `SPLIT_ORDER_RULE` to the prompt, only when the flag is on.
- Flag off, no `depends_on`, or the board unreachable: the Architect prompt is byte for byte the one
  from before the patch.

## Plan lint

`data-tables/ops/plan_dependency_lint_patch.py` mirrors `find_part_order_problems`
(`board_dependencies.py:400`) inside `Evaluate Architect`: a task of part K may name only its own
earlier tasks or what an earlier part produced. A bad plan goes back to the Architect once
(`LINT_RETRY_LIMIT = 1`, bounded by `GATE_RETRY_ROUTED`), the second failure follows the existing
plan-gate path. The patch needs `plan_gate_retry_patch.py` first; with the flag off
`GATE_RETRY_ROUTED` stays false and the plan path is the old one.

## Flag `mission_dependencies`

`ahawr-runner/src/ahawr_runner/features_defaults.json:29-30` — `"mission_dependencies":
{"enabled": false}`, read by `_feature_enabled` (`board_api.py:89`). Off means: no `depends_on`,
`dependency_state` or `dependency_reason` keys in any answer (absent, not null), no guard on moves,
drops or starts, no Architect block, no plan lint — the behaviour is byte for byte the old one.

## Operator steps

The rule, the API fields and the dashboard part are in the runner; the workflow side is the three
patch scripts in `data-tables/ops`, not yet run by `launch_mission.sh`. One window, queue paused
(`/pause <reason>`, `docs/telegram-bot.md`):

1. Store migration — nothing to run by hand: `_ensure_queue_columns` (`board_pg.py:422`) adds the
   column with its default when the board store is constructed; the step is the runner rebuild in the
   window, requested through `POST /v1/host-actions` on the runner.
2. Export a fresh copy of the live workflow into `data-tables/ops`. Not `AHAWR_Workflow.json`: that
   file is not what runs.
3. Patch the copy, in this order; each is idempotent and asserts every anchor exactly once:
   `python data-tables/ops/split_dependencies_patch.py <copy.json>`, then
   `architect_prompt_dependency_block.py`, then `plan_dependency_lint_patch.py` (needs
   `plan_gate_retry_patch.py` first). Check: `node data-tables/ops/js_syntax_check.js <copy.json>`.
4. Import the patched copy in the n8n interface while the queue is paused, then `/pause off`.
5. Turn the flag on in `features_json` (`ahawr-runner/docs/features-config.md`), or in one mission's
   `mission_features_json`. Watch one mission before the others.

Rollback: set the flag back to false — guard, block and lint stop at once, no re-import; undoing the patches means importing the pre-launch backup `wf_before_<mission>.json`.
