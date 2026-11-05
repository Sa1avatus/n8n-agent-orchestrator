# Task: document the retry budget under a precise heading

`src/docs/retries.md` describes the retry policy, but it does not say how many tries a call gets. The code in `src/retry.py` sets `MAX_ATTEMPTS`. Read that constant and document it.

Add a new section to `src/docs/retries.md`, after the `## Backoff` section, with this exact heading line:

`## Retry budget`

The section body must state the total number of attempts as a number followed by the word `attempts`, the first try included (for example, `3 attempts`). Use the value of `MAX_ATTEMPTS` in `src/retry.py`.

Rules:
- This is a docs-only change. Change `src/docs/retries.md` only. Do not edit `src/retry.py` or any other file.
- Keep the existing `# Retries`, `## Backoff` and its text unchanged.
- The heading must be exactly `## Retry budget`: level 2, capital R and lower-case b, no extra words.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: the lines you added to `src/docs/retries.md` (file:line) and the check result.
