# Task: `humanize` must show hours, and the test must follow the fix

`src/durations.py` turns a number of seconds into a short text. Above one hour it shows the total in minutes: `humanize(3600)` gives `"60m"`, but the spec says `"1h 0m"`. The spec for hours is: whole hours, then the remaining whole minutes, seconds dropped. `humanize(3661)` must give `"1h 1m"`.

The old test in `tests/test_durations.py` asserts the wrong values `"60m"` and `"61m"`. It encodes the bug. If you fix `src/durations.py` alone, that test fails. You must update the test to the new spec, too.

Rules:
- Change `src/durations.py` so that `humanize` returns the spec values for every case in the check.
- Change `tests/test_durations.py` so that its hour assertions match the spec. Do not delete the test.
- Keep the function name and signature. Use the standard library only.
- Seconds and minute formats below one hour stay as they are.

Proof: `python3 check.py` prints `PASS` and exits 0. The check also runs the unit tests in `tests/`.

Final answer: the changed lines of `src/durations.py` and of `tests/test_durations.py` (file:line), and the check result.
