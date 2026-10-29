# Board: guards

> Note: the reference image `guards-reference.png` named below is an
> html-rendered stand-in of the detail view of the Guards page, not a screenshot
> captured from the live dashboard, which is unreachable in this environment.
> Replace it with a real capture against the live page before the contract gates a review.

## Guards page
- Open: route /#guards, viewport 1280x800
- Layout: the Guards tab of the dashboard shell (button id tabguards, shown when
  DASHBOARD_GUARDS_TAB is on); the page header shows the master state ("guards on"
  / "guards off"), a "dry run" mark, and the counts of ok, warning, error and
  disabled guards. The view tabs "Detail", "Cards", "Flow" and "Timelines" sit
  above the content; "Detail" is the default and shows a split layout: a list of
  guards on the left (230 px, one button per guard with a status LED and the guard
  name) and on the right the detail of the selected guard — its name and status
  badge, an on/off switch, the description text and last check time, a Settings
  card (Poll interval, Health window, Save), a Health card (ok share, checks and
  errors in the window) and a Recent actions card.
- Reference image: guards-reference.png
- C1: the page header shows the master state: "guards on" when the supervisor is
  enabled, "guards off" when disabled; a "dry run" mark follows when the supervisor
  is in dry-run mode.
- C2: the page header shows the summary counts: a number followed by "ok", a number
  followed by "warning", a number followed by "error", and a number followed by
  "disabled".
- C3: the view tabs "Detail", "Cards", "Flow" and "Timelines" are shown; "Detail" is
  the default and presents the list on the left and the selected guard's detail on
  the right in a split layout (single column below 760 px).
- C4: the left list shows one button per guard with a status LED and the guard name;
  the selected guard's button is highlighted.
- C5: the detail header shows the guard name (monospace), a status badge with a shape
  and the status word — "ok", "warning", "error" or "disabled" (the off state) — and
  a "dry run" mark when that guard is in dry-run mode.
- C6: an on/off switch (a toggle with the HTML role `switch`) is present in the
  detail header; it is disabled when DASHBOARD_GUARDS_EDIT is off, and toggling it
  sends PUT /api/guards/{name} with the `X-AHAWR-Dashboard` header.
- C7: the description text of the selected guard follows the header: "Restarts the
  mission queue when it stops" (queue_restart), "Watches the host watchers"
  (host_heartbeat), "Forwards notices to Telegram" (tg_relay), "Posts mission start
  and end notices" (mission_notice), "Stops idle runs after a timeout" (idle),
  followed by "last" and the time since the last check ("ago"), or "—" when the
  guard has never run.
- C8: when DASHBOARD_GUARDS_EDIT is off, the Settings card (titled "Settings") shows
  "Poll interval" followed by the interval value and "s" as read-only text, then
  "Health window" followed by the window value, then the notice "Editing is off
  (DASHBOARD_GUARDS_EDIT)."; there is no "Save" button and no input fields or select
  elements.
- C9: when DASHBOARD_GUARDS_EDIT is on, the Settings card shows "Poll interval" as a
  number input (min 1, max 86400) followed by "s", "Health window" as a select with
  the options "1 hour", "24 hours" and "7 days" (values 1h, 24h, 7d), and a "Save"
  button.
- C10: the Health card (titled "Health") shows the ok share as a percentage or "—",
  and a sub-line with the checks count, "checks", the errors count, "error", "in" and
  the health window ("1h", "24h" or "7d"); below it a strip of the last checks (status
  indicators only, no text).
- C11: the Recent actions card (titled "Recent actions") lists the last actions (up to
  six) as a time and the action text in monospace, or shows "—" when the guard has no
  actions.
- C12: the page never shows environment variable values: GET /api/guards returns
  enabled, dry_run, running, the per-guard state and counts and the health summary
  only — no DASHBOARD_GUARDS_* values, tokens or keys.
