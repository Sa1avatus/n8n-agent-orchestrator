# Dashboard: mobile layout

> Note: the reference image `mobile-runs-reference.png` named below is a capture of the
> dashboard at 390x844 taken BEFORE the mission `ahawr-dashboard-lan-mobile` finished, not
> the agreed final look. It shows the layout the checks start from (stacked header, run
> list above the tabs, run summary below). Use it for the overall structure only; the
> checks below decide the verdict. Replace it with a capture of the accepted mobile layout.

## Runs page on a phone
- Open: route /, viewport 390x844
- Layout: a single column. The header shows "AHAWR" with the word "trajectory", a theme
  button ("Theme: Auto", "Theme: Light" or "Theme: Dark"), and the status words of the
  services ("claude-runner: ok", "hermes: ok"). Below it a search field for events, then
  the run list (one compact card or row per run: a title, a status dot with the status
  word, role and model labels, the duration), then the tabs "Runs", "Missions",
  "Providers" and "Guards", then the summary and the step log of the selected run in the
  same column.
- Reference image: mobile-runs-reference.png
- C1: nothing is cut off on the right edge: no text, button or table column is clipped
  and the page has no horizontal scroll bar; a long code or output block may scroll
  inside its own frame.
- C2: each run in the list is one compact card or row with its title, status (a dot or
  badge and the word, for example "running" or "completed"), role label and duration;
  the rows are not a wide table that needs sideways scrolling.
- C3: the run summary is a single column: the title, the "running" or final status, the
  "Stop" button when the run is running, then the model, session, cwd and start time,
  then the counters (turns, steps, wall time, tokens, cache hit, tok/s); no two columns
  of the summary sit side by side.
- C4: the main text is readable on the phone: body text, status words and tab labels
  look at least 14 px high, no line of normal text is tiny or overlapping another.
- C5: the tabs "Runs", "Missions", "Providers" and "Guards" are all visible in one
  row or wrap to a second row without overlapping, and each tab is tall enough to tap
  (about 44 px high or more).
- C6: the theme button ("Theme: Auto" or the chosen theme) is visible in the header and
  is large enough to tap; the colors of the page are readable (dark text on a light
  page or light text on a dark page, never grey on grey).
- C7: the "Stop" button of a running run is visible in the summary and looks like a
  button (outlined, with the stop square and the word "Stop"); it is not hidden
  behind another element.
- C8: the page shows no secret values: no token, password, API key or cookie value
  appears in the text of the page.
