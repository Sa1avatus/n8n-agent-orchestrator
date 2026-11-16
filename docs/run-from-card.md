# Run next / Run now on the kanban card

Flag `run_mission_from_card` (Features panel, OFF by default). With the flag off the kanban markup and the API answers are unchanged:
the two routes answer like a path nothing mounted (404).

- `POST /api/board/missions/{id}/run-next` moves the mission to position 0 of `board.queue` through `store.move_queue`.
- `POST /api/board/missions/{id}/run-now` does the same, then enqueues the `resume` ops action when the process panel says no mission
  runs. When one runs, nothing is stopped: the answer is `{"status": "after_running", "offer_action": "pause_at_boundary"}`.
- Both need the header `X-AHAWR-Dashboard: 1` and a queue version (`expected_version`, the current one when omitted; a stale one is 409).
  The read-only port 8702 answers 403.
- A mission is refused with a reason code (`disabled`, `failed_without_retry`, `running`, `completed`) from stored data (the mission and
  queue rows, the Live State status, the `review_status` of the current task), never from a reason text.
- UI: `board_run_mission.js` adds the two buttons to the cards of the Running, Queued and Not completed columns after a confirm step;
  they are disabled with the reason as the title; `board_readonly.js` hides them on the read-only page. Texts: keys `action.run_*` and
  `run.*` in `board_i18n_en.json`, `board_i18n_ru.json` and `BUNDLED_EN`.
- Tests: `tests/test_board_run_mission.py`, `tests/board_run_mission.test.js`.

## Turning the flag on

1. The workflow's `Constants` node rejects a feature it does not know, so every launch fails if the flag is set first. The list is `CORE_FEATURES`
   in `data-tables/ops/features_config_patch.py` (outside git); `launch_mission.sh` applies it at each launch.
2. Check that the live `Constants` lists `run_mission_from_card` in `FEATURE_DEFAULTS`.
3. Only then add `"run_mission_from_card": {"enabled": true}` to `features_json` (the Features panel or the config row).
