# Board part 2 fixtures — trimmed live response shapes

These fixtures mirror the JSON the read-only board API
(`GET /api/board/...`) returns, with real values trimmed and anonymised:
no tokens, DSNs, session ids, run ids or real paths — fake ids and neutral
values only. The hostile title (`<img src=x onerror=alert(1)>`) is a test
value the fixture carries on purpose (XSS probe), not a secret.

**Source.** The live dashboard at `http://127.0.0.1:8701/api/board/...` was
unreachable at capture time (curl: no response, exit 000), so these fixtures
are built from the handler code of T002 (`claude-runner/src/claude_runner/`):

- `missions.json` — `GET /api/board/missions` (`board_api.py` `list_missions`,
  `store.mission_groups()` + `mission.model_dump()`);
- `plan.json` — `GET /api/board/missions/{id}/plan` (`board_api.py` `get_plan`:
  `plan`/`version`/`tasks`, tasks are `TaskView.model_dump()`);
- `task_view.json` — one `TaskView.model_dump()` row (the same shape the plan
  `tasks` array holds and the mission detail's `task_views` uses);
- `task_stats.json` — `GET /api/board/missions/{id}/tasks/{key}/stats`
  (`board_stats.task_stats`);
- `thinking_stats.json` — `POST /api/board/thinking/stats`
  (`board_think.variant_stats` over the `attempts` rows);
- `readiness.json` — `GET /api/board/missions/{id}/readiness`
  (`board_ready.collect_readiness` → `_summarize`);
- `changelog.json` — `GET /api/board/changelog` (`ChangelogEntry.model_dump()`
  per row, newest first).

Scenario covered by the fixtures: one mission in `running` (Live State
`status == "active"`), one task that already ran (`accepted`, review score
recorded), one not-completed task (`not_accepted`), one readiness error
(`working_folder` does not exist), and one changelog entry with a diff
(`before_json` / `after_json` both present).
