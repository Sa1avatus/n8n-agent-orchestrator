# Task: money is kept in integer cents across two files

Amounts are typed as text like `"19.99"`. Today `parse_amount` in `src/money.py` returns a float in dollars, and `total_cents` in `src/ledger.py` multiplies the sum by 100 to get cents. Floats make the sums drift, and the multiplication is in the wrong place.

The new rule: `parse_amount` returns the amount as an `int` number of cents, exact, with no float arithmetic. `total_cents` must then add those cents directly, without any multiplication. Both files change together: the callee sets the new contract and the caller must follow it.

Examples the check uses:
- `parse_amount("1.10")` gives `110`
- `parse_amount("0.07")` gives `7`
- `parse_amount("12")` gives `1200`
- `total_cents(["19.99", "0.01"])` gives `2000`

Rules:
- Change `src/money.py` and `src/ledger.py`. Do not change any other file.
- Use `decimal.Decimal` or string handling from the standard library. Do not use `float` for the result.
- Keep the function names and that `total_cents` takes a list of text lines.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: the changed lines of `src/money.py` and `src/ledger.py` (file:line) and the check result.
