# Board: mission dependencies

> Note: the reference image `mission-dependencies-reference.png` named below does not
> exist yet. The capture needs playwright/chromium (`python -m ahawr_runner.qa capture
> --contract ahawr-mission-dependencies` with `QA_STACK_MODE=inprocess`), which is not
> installed in this environment; until the operator captures it from the in-process
> dashboard with the fixture `mission_dependencies`, the validator reports the missing
> image as the only open blocker of this contract.

## Queue list: the after line and the waiting chip
- Open: route /static/board.html#board, viewport 1280x800
- Layout: the queue list in `board.queue` order; every card whose queue row carries
  `depends_on` gets the dependency line and chip after the card title
  (board_queue.js:120, board_mission_dependencies.js:34); a card with an empty list reads
  "no mission dependencies".
- Reference image: mission-dependencies-reference.png
- C1: the "Waiting on the base" card shows the line "after: dep-base" - t("q.dep")
  "after: {ids}" (board_i18n_en.json:14), rendered by renderDependencyLine
  (board_mission_dependencies.js:37).
- C2: the same card carries the chip "waiting for dep-base" - t("q.dep.waiting")
  (board_i18n_en.json:14), class `q-dep-waiting` (board_mission_dependencies.js:30,49-50);
  the API answer GET /api/board carries `depends_on` and `dependency_state` for every
  queue row (board_api.py:436-453).

## Kanban view: the chip of a blocked card
- Open: route /static/board.html#board/view/kan, viewport 1280x800
- Layout: kanban cards in queue order with the same dependency line and chip as the
  queue cards (board_view_kanban.js:74).
- Reference image: mission-dependencies-reference.png
- C3: the "Blocked by the failed run" card (dep-blocked, its dependency dep-failed has
  a failed live state in the fixture) shows the chip "blocked by dep-failed: a dependency
  is not completed" - t("q.dep.blocked") (board_i18n_en.json:14).

## Mission editor: the "After missions" field and refused saves
- Open: route /static/board.html#board/dep-free, click "✎ Edit launch settings", click
  "Next ›", viewport 1280x800
- Layout: the launch settings window with the pages Mission, Execution, Context, Review
  (t("ew.s1")-"ew.s4"); the Execution page holds the "After missions" label and a text
  input named `depends_on` (board_edit_win.js:1033); "Save changes" POSTs
  /api/board/missions/<id>.
- Reference image: mission-dependencies-reference.png
- C4: with the mission_dependencies flag on the field label is exactly "After missions"
  - t("m.dep") (board_i18n_en.json:14) - and the input name is `depends_on`
  (board_actions.js:497-502).
- C5: a save naming an unknown id shows the reason "unknown mission dependency
  no-such-mission: it is neither in the queue nor completed" (board_dependencies.py:151-152)
  in the window error line #ew-err (board_mission_save.js:412-418).
- C6: a save that closes a loop shows "the mission dependency closes a cycle:
  dep-base -> dep-waiting -> dep-base" (board_dependencies.py:161) and the queue answer
  of GET /api/board keeps the old ids.

## Kanban drag: a move before a dependency
- Open: route /static/board.html#board/view/kan, drag "Waiting on the base" onto the
  title of "Dependency base", viewport 1280x800
- Layout: the kanban cards are drag-and-drop cards (board_dnd.js); a refused move shows
  a visible state notice at the top of the board (showState, board_actions.js:129-134).
- Reference image: mission-dependencies-reference.png
- C7: the notice reads exactly "The move is refused: dep-waiting must run after dep-base"
  - t("dep.order") (board_i18n_en.json:14), chosen by dependencyRefusalMessage
  (board_mission_dependencies.js:137-154) and shown by board_dnd.js:152.
- C8: POST /api/board/queue/move (board_api.py:1922-1929) refuses with `blocked_by`
  ["dep-base"] and stores nothing: GET /api/board still lists dep-base before
  dep-waiting in the same positions.

## Run next and the "Run anyway" confirm
- Open: route /static/board.html#board/view/kan, click "to the top" on the
  "Blocked by the failed run" card, viewport 1280x800
- Layout: the native browser confirm dialog over the kanban cards, holding the one-line
  question and the accept/decline buttons.
- Reference image: mission-dependencies-reference.png
- C9: REQUIRED behaviour, currently failing: run next of a waiting or blocked mission is
  refused with the code "guard" and asks "run anyway" - t("dep.anyway") (board_i18n_en.json:14) -
  before the accepted confirm resends with `run_anyway: true`.  Today the card button
  "run next" (t("action.run_next", board_i18n_en.json:29) POSTs
  /api/board/missions/<id>/run-next) has no dependency guard: the route checks only
  run_mission_from_card and the eligibility at board_run_mission.py:153, so a blocked
  mission moves to the front without the question - board_api.py:1808-1822.  This item
  fails until the guard is added (a code task outside QA scope).
- C10: the guarded queue action POST /api/board/queue/first (board_api.py:1972-1984)
  refuses a waiting or blocked mission with the code "guard" and a `dependency_state`
  payload (board_api.py:569-577).
- C11: the confirm question is exactly "The dependencies are not completed: run anyway?
  (dep-failed)" - t("dep.anyway") (board_i18n_en.json:14) plus the blocked ids in
  brackets (dependencyRunConfirm, board_mission_dependencies.js:129-131).
- C12: an accepted confirm resends the same POST with `run_anyway: true`
  (board_actions.js:294-296) and answers 2xx; a declined confirm sends no second
  request (board_actions.js:283-287).
