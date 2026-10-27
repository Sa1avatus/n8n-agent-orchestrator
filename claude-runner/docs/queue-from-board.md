# Queue from the board

Design of the mission queue that the queue shell reads from `board.queue`
(mission `ahawr-queue-from-board`). The shell keeps a static list today; this
document describes the list it reads from the board and the rules for picking
the next mission.

## Two queues today

- **`real_queue.sh` (`data-tables/ops/`, not in git).** The order of the
  missions is the `QUEUE` list in the script. Companion lists set the rules per
  mission: `RETRY_FAILED` (a failed mission is retried), `ATTEMPTS5` and
  `ATTEMPTS10` (attempts per task; default 3), `CLOUD_WORKER`, `NEEDS_FILE`.
  The script reads the list once at start. Changing it means a restart of the
  queue process.
- **Hand edits and appender scripts.** The list is edited by hand, or by
  one-off appender scripts such as `_queue_append_two.sh` and
  `_queue_insert_ops.sh`. These edit `real_queue.sh` while the queue is
  stopped, then restart it. Each one is a separate operator action.
- **`board.queue` (the database, shown as the Kanban queue).** Entries have
  `position`, `mission_id`, `enabled`, `attempts_per_task`, `retry_failed` and
  `note`. The store changes positions through its methods, and the dashboard
  API versions every write (`expected_version`). Nothing reads this table to
  start a mission today.
- **`sync_board_queue.py` (stop-gap, `data-tables/ops/`).** It copies the
  `QUEUE` list of `real_queue.sh` into `board.queue` in one POST with the
  current version. It applies the same skip rules as the shell (completed
  missions, and failed missions not in `RETRY_FAILED`). Nothing runs it
  automatically, so the two queues drift until someone runs it by hand. It is
  removed once the shell reads the board.

## New flow

1. A mission starts or ends (the queue shell logs `started <mission>` or
   `ended <mission>:`).
2. The shell runs `queue_board.py next`.
3. `queue_board.py` sends `GET http://127.0.0.1:8701/api/board/queue` and
   reads `queue` and `version`.
4. `queue_board.py` applies the next-mission rules (below) and prints one
   `mission_id`, or nothing when no mission is due.
5. The shell launches the mission with `launch_mission.sh`, using the
   attempts and retry flag of its entry. Empty fields keep the shell defaults.
6. When the mission ends `completed`, the shell removes its entry with
   `DELETE http://127.0.0.1:8701/api/board/queue/<mission_id>`.
   A failed removal writes one ALERT line.
7. The loop goes back to step 1.

**Next-mission rules.** `queue_board.py` picks the first entry that matches,
checked in this order:

1. An entry whose mission is `active` (an interrupted run) is picked first, so
   the run resumes before anything else starts.
2. Otherwise the first entry in `position` order with `enabled` true, whose
   state is not `completed`.
3. A state `failed` entry is picked only when its `retry_failed` is true.
   Otherwise it is skipped, and the operator decides on a retry.
4. An entry with an empty `attempts_per_task` uses the shell default (3).
   An empty `retry_failed` means false. An empty `note` has no effect.

**Version of removal.** The removal in step 6 is meant to pass the version read
in step 3, so a queue changed by the dashboard in the meantime is not
overwritten. The current `DELETE /api/board/queue/{mission_id}` route reads the
version on the server and accepts no client version. Adding one changes a
public API, so it is an operator decision and is not part of this design.

## Fallback

- **Three tries.** A failed or invalid answer from the GET in step 3 is retried
  up to three times in total, with a fixed delay of `QUEUE_FETCH_DELAY_SECONDS`
  between the tries.
- **Invalid answer.** A reply that is not JSON, has no `queue` list, or has an
  entry without `mission_id` counts as a failed try.
- **Static list.** When all three tries fail, the shell uses its static `QUEUE`
  list and the existing rules of `real_queue.sh`. The queue does not stop.
- **One ALERT line.** The shell writes one `ALERT` line to `real_queue.alert`
  for the fallback (the same `alert()` function that the shell uses today). It
  writes no more lines for the same mission.
- **Failed removal.** A failed DELETE in step 6 writes an `ALERT` line. It does
  not stop the queue and does not retry the removal in a loop.
- **Never a stop.** No failure of `queue_board.py` or of the board API stops
  the queue. The existing stop rule (two missions not completed in a row) is
  unchanged.

## What takes effect when

- The list is read at each pick (step 2), not once at start. A change made on
  the board, or a change to the entries, takes effect at the next pick.
- A change never takes effect during a mission. A running mission is not
  stopped, moved or removed by a board change. Its entry is removed only when
  it completes (step 6).
- A change made before a pick, but after the mission before it ended, is seen
  by that pick.

## Flag

- `QUEUE_FROM_BOARD` is ON by default. Unset means on.
- `QUEUE_FROM_BOARD=0` turns it off. The shell then runs exactly as today: the
  static `QUEUE` list, the same rules, the same log lines, no call to
  `queue_board.py`. The behaviour is byte-for-byte unchanged, and this is
  tested by comparing the log with the flag off.
- `queue_watch.py` follows the same flag. With `QUEUE_FROM_BOARD=0` it does
  not read `board.queue` and behaves exactly as today.

## Lists of real_queue.sh

Each list of `real_queue.sh` (`data-tables/ops/`, lines 64-69) and where its
setting goes. "Fallback only" means the list stays in the shell for the static
fallback above and is not read for a board pick.

| List | Meaning | New place |
| --- | --- | --- |
| `QUEUE` | The order of the missions | `board.queue` rows: `position` and `mission_id`. Fallback only. |
| `ATTEMPTS5` | 5 attempts per task | `attempts_per_task` = 5. Fallback only. |
| `ATTEMPTS10` | 10 attempts per task | `attempts_per_task` = 10. Fallback only. |
| `MAXTASKS30` | Task limit 30 instead of 20 | No QueueEntry field. Stays in the shell. |
| `CLOUD_WORKER` | Worker on the cloud model (`claude-haiku-5-5`) | No QueueEntry field. Stays in the shell. |
| `RETRY_FAILED` | A failed mission is retried | `retry_failed` = true. Fallback only. |
| `NEEDS_FILE` | Mission is skipped unless its data file exists | No QueueEntry field. Stays in the shell. |

`enabled` has no list counterpart: an entry with `enabled` false is held back.
`note` carries a free-text comment; neither has a list in `real_queue.sh`.
An empty `attempts_per_task` keeps the shell default of 3.

## Obsolete operator scripts

Nothing is deleted. Each script below is replaced for the board flow and is
kept for reference.

| Script (`data-tables/ops/`) | What it did | Replacement |
| --- | --- | --- |
| `sync_board_queue.py` | Copied `QUEUE` into `board.queue` in one POST | `python queue_board.py sync-from-shell` |
| `_queue_append_qa_gate.sh` | Inserted `ahawr-qa-gate` into `QUEUE` and `ATTEMPTS5`, restarted the queue | `python queue_board.py add ahawr-qa-gate --after ahawr-features-config` ; no restart |
| `_queue_append_qa_layers.sh` | Same for `ahawr-qa-layers` | `python queue_board.py add ahawr-qa-layers --after ahawr-features-config` ; no restart |
| `_queue_append_qa_smoke.sh` | Same for `ahawr-qa-smoke` | `python queue_board.py add ahawr-qa-smoke --after ahawr-guards-migration` ; no restart |
| `_queue_append_report_pack.sh` | Same for `ahawr-report-pack` | `python queue_board.py add ahawr-report-pack --after ahawr-guards-migration` ; no restart |
| `_queue_append_two.sh` | Inserted `ahawr-qa-layers` and `ahawr-queue-from-board` into `QUEUE` and `ATTEMPTS5`, restarted the queue | `python queue_board.py add ahawr-qa-layers --after ahawr-features-config` and `add ahawr-queue-from-board --after ahawr-qa-layers` ; no restart |
| `_queue_insert_ops.sh` | Inserted `ahawr-dashboard-ops-control` into `QUEUE` and `ATTEMPTS5` while the queue was stopped | `python queue_board.py add ahawr-dashboard-ops-control --after ahawr-dashboard-mission-board` ; no restart |
| `_queue_reorder.sh` | Moved missions in `QUEUE` and restarted the queue | `python queue_board.py move <mission_id> --after <other_id>` ; no restart |
| `_queue_run_bench_hour.sh` | Added one non-AHAWR mission to `QUEUE` and started `queue_watch` | `python queue_board.py add bench-rag-api-local-r2 --after ahawr-guards-migration` ; no restart |
| `_queue_resume_after_commit.sh` | Added missions to `QUEUE` and started `queue_watch` | None needed: the shell reads the board at each pick |
| `_restart_queue_at_end.sh` | Restarted the queue after a mission so it read the new list | None needed: a board change takes effect at the next pick |

`_restart_haiku.sh` and `_restart_queue_once.sh` are not queue-list editors: they
restart the haiku window or the queue processes respectively, so they stay
unchanged.

## Migration for the operator

NOT RUN. The steps are a plan; none has been executed. Stop the queue for step
3 and make sure no mission is running.

1. Seed the board from the shell: `python queue_board.py sync-from-shell`.
   It replaces `board.queue` with the `QUEUE` order, using the version read
   from the dashboard.
2. Check the seed: `python queue_board.py list`. Compare its order
   and `attempts_per_task` with `QUEUE` and `ATTEMPTS5` in `real_queue.sh`.
3. At a queue pause, keep the old file and put the board-reading shell in its
   place:
   `cp data-tables/ops/real_queue.sh data-tables/ops/real_queue.sh.bak-board-migration`
   and then `cp data-tables/ops/real_queue_board.sh data-tables/ops/real_queue.sh`.
   `real_queue_board.sh` is in `data-tables/ops/`.
4. Start the queue. Its log shows `queue_board.py next` at each pick.

## Rollback

Both commands are one line each. Use the flag first: it needs no file change.

- Flag: `export QUEUE_FROM_BOARD=0` in the shell that starts `queue_watch.py`,
  then restart the queue. The static `QUEUE` list is used again.
- Old file: `cp data-tables/ops/real_queue.sh.bak-board-migration data-tables/ops/real_queue.sh`
  with the queue stopped, then restart the queue.

## Not done

- No database column or schema change. `board.queue` keeps the six existing
  fields: `position`, `mission_id`, `enabled`, `attempts_per_task`,
  `retry_failed` and `note`.
- The `board.queue` columns `max_tasks`, `cloud_worker` and `needs_file` are
  not added; `MAXTASKS30`, `CLOUD_WORKER` and `NEEDS_FILE` have no QueueEntry
  field and stay in the shell. Adding the columns needs an operator decision
  because it changes the schema.
- The static fallback stays in `real_queue.sh`.
