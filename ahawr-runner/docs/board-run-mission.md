# Run a mission from its card

The board card of a mission has two buttons, **Run next** and **Run now**. Code: `src/ahawr_runner/board_run_mission.py` (rules), `src/ahawr_runner/board_api.py` (routes). Paths below are relative to `ahawr-runner/`.

## Flag

The feature is the flag `run_mission_from_card` in `features_json` (the global value) or `mission_features_json` (a per-mission override); see [features-config.md](features-config.md).

- Default: the shipped default in `src/ahawr_runner/features_defaults.json` is `"enabled": true` (the operator turned it on on 2026-11-17, and `tests/test_features_config.py` pins it in `ON_BY_DEFAULT`). The flag is not OFF by default in this version.
- With the flag off, or while the board store cannot be read (unreadable flags count as off), both routes answer exactly like a path nothing mounted: HTTP 404, and the board behaves as if they did not exist.
- To turn it off or on, the operator edits the feature in the board's features section, or writes `{"run_mission_from_card": {"enabled": false}}` (or `true`) into `features_json`; a mission can override it in `mission_features_json`. Both texts are validated on save. Nothing is edited in the defaults file.

## What the buttons do

- **Run next**: sends the mission to position 0 of `board.queue` (through `store.move_queue`, so positions stay contiguous). It does not start anything by itself.
- **Run now**: does "Run next", then looks at the process panel (`queue.alive` of the newest host snapshot):
  - nothing is running: the existing `resume` ops action is enqueued once; the host agent executes the row (the runner runs no shell command). Answer status `resume_queued`.
  - a mission is running: nothing is stopped and `resume` is not enqueued. Answer status `after_running` ("starts after the running one") with `offer_action: "pause_at_boundary"`. A running mission is stopped only if the operator chooses that offer, which pauses at the task boundary.

## Routes

Both are `POST` under `/api/board`, body is a JSON object, optional `expected_version` (string or null; the queue version, current one when omitted).

- `POST /api/board/missions/{mission_id}/run-next`: answer `{"queue": [...], "version": ...}`.
- `POST /api/board/missions/{mission_id}/run-now`: answer `{"status": "resume_queued" | "after_running", "mission_id": ..., ...}` plus `queue` and `version`. `resume_queued` also carries `action` and `command`; `after_running` carries `offer_action`. An unreachable ops store gives 503.

Answers:

| Case | HTTP |
|---|---|
| flag off or board store unreadable | 404 |
| header `X-AHAWR-Dashboard: 1` missing | 403 (guard error) |
| read-only port 8702 (`DASHBOARD_READONLY=1`) | 403 `dashboard is in read-only mode` (every write is refused there) |
| mission is not eligible (see below) | 403 guard error with the reason code in the message |
| stale `expected_version` (queue changed) | 409 conflict |
| mission not on the board / no queue row | 404 |
| empty mission id, bad `expected_version` type | 400 |

The UI adds the queue row (`add_queue`) before the move when the mission has none.

## Eligibility

Decided in `mission_run_eligibility` from stored rows only; the free-text `review_reason` is never parsed. A refused run carries one of the codes `disabled`, `failed_without_retry`, `running`, `completed`:

- `disabled`: the mission row or its queue row has `enabled` false.
- `running`: the Live State status is `active`, was written recently (not stale) and the mission has run.
- `completed`: the Live State status is `completed`, or (no status written) the current task's stored `review_status` is `pass`.
- `failed_without_retry`: the status is `failed` or `stopped`, or the current task's `review_status` is `needs_changes`, and the queue row has no `retry_failed` flag. With `retry_failed` the mission may run again.
- A mission with no Live State row has never started and is eligible.
