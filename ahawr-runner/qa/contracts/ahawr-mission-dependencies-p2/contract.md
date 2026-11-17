# Board: mission dependencies

> Note: the reference image `mission-dependencies-reference.png` named below does not
> exist yet. The capture needs playwright/chromium (`python -m ahawr_runner.qa capture
> --contract ahawr-mission-dependencies-p2` with `QA_STACK_MODE=inprocess`), which is
> not installed in this environment; until the operator captures it from the in-process
> dashboard with the fixture `mission_dependencies`, the validator reports the missing
> image as the only open blocker of this contract.

## Mission editor with the "After missions" field
- Open: route /static/board.html#board/dep-waiting, click "✎ Edit launch settings",
  click "Next ›", viewport 1280x800
- Layout: the mission screen shows the configuration box with an "After missions" row:
  a label and a text input named `depends_on` whose value is the ids joined by ", ";
  the hint is the tooltip of the input; the same field opens in the edit window on the
  Execution page.
- Reference image: mission-dependencies-reference.png
- C1: the configuration box contains an "After missions" label (t("m.dep") "After missions")
  and a text input named `depends_on`; for dep-waiting the field shows "dep-base".
- C2: the tooltip of the "After missions" input is exactly t("m.dep.hint"): "Ids of the
  missions that must run before this one; a new mission is placed after the last dependency,
  and a move that breaks the order is refused." (visible in the screenshot or the DOM).
- C3: the edit window opened by "✎ Edit launch settings" shows the "After missions" field
  on the Execution page; "Save changes" answers 2xx on POST /api/board/missions/dep-waiting
  (API answer) and the stored `depends_on` is the typed ids.
- C4: a dependency save the guard refuses answers with a reason naming the mission, not a
  silent success: the state banner or #ew-err shows the refusal text (screenshot) and the
  queue keeps the old ids (API answer GET /api/board).

## Mission creation form with the "After missions" field
- Open: route /static/board.html#board, click "New mission", viewport 1280x800
- Layout: the new-mission form (mission_id, name, objective) with the "After missions"
  label, a text input named `depends_on` and a small error element inserted before the
  "create" button.
- Reference image: mission-dependencies-reference.png
- C5: with the mission_dependencies flag on the form shows the "After missions" label
  (t("m.dep") "After missions") and an empty `depends_on` input.
- C6: the tooltip of the input is exactly the t("m.dep.hint") text, the same hint as the
  editor field.
- C7: "create" POSTs /api/board/missions/create with `depends_on` as a list and answers
  2xx (API answer); copying a mission prefills the field with the source's ids.
- C8: the card of a created mission with a dependency shows the t("q.dep") line
  "after: {ids}" in the queue (screenshot, API answer GET /api/board carries depends_on).

## The "Move them too" dialog
- Open: route /static/board.html#board, click "to the end" on the "Dependency base" card,
  viewport 1280x800
- Layout: the queue list with a native browser confirm dialog over it; the dialog holds
  the one-line question and the accept/decline buttons.
- Reference image: mission-dependencies-reference.png
- C9: a move of a mission that other missions depend on is refused: the POST of the move
  answers with a guard refusal naming the dependents in `blocked_by` (API answer).
- C10: the dialog question is exactly t("dep.group"): "Do you want to move them too?
  {ids} ({n}) depend on it." — on the fixture "Do you want to move them too? dep-waiting
  (1) depend on it." (screenshot of the dialog).
- C11: "Move them too" (t("btn.dep.group") "Move them too") resends the same move with
  `move_dependents: true` and answers 2xx; the dependents land next to the moved card
  (API answer GET /api/board shows the new queue order).
- C12: "Move only this mission" (t("btn.dep.only") "Move only this mission") sends no
  second request (no second POST in the API answers) and shows the refusal banner with
  t("dep.order"): "The move is refused: {id} must run after {ids}." (screenshot).

## The "Run anyway" confirm
- Open: route /static/board.html#board/view/kan, click "to the top" on the
  "Blocked by the failed run" card, viewport 1280x800
- Layout: the kanban view where a card with a failed dependency carries the after line
  and the blocked chip; the native confirm dialog asks above the cards.
- Reference image: mission-dependencies-reference.png
- C13: a waiting or blocked card carries a dependency chip; the dep-blocked card shows
  t("q.dep.blocked"): "blocked by dep-failed: a dependency is not completed" (screenshot,
  API answer GET /api/board carries dependency_state).
- C14: the guarded action of a blocked mission is refused with a `dependency_state`
  payload (API answer POST /api/board/queue/first).
- C15: the confirm question is exactly t("dep.anyway"): "The dependencies are not
  completed: run anyway?" with the blocked ids appended in brackets (screenshot of the
  dialog).
- C16: "Run anyway" (t("btn.dep.anyway") "Run anyway") resends the same request with
  `run_anyway: true` and answers 2xx (API answer); declining sends no second request.
