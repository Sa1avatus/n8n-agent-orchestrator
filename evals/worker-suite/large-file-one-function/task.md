# Task: fix `total_weight` in a large generated file

`src/records_table.py` is a generated table of about 3200 rows. It is too large to read in one go, so do not open it whole. Locate the function with `grep -n "def total_weight" src/records_table.py` and read only the lines around it.

The bug: `total_weight` returns the sum of the weights of every row except the last one. It must return the sum of the weights of all rows, and `total_weight([])` must return 0.

Rules:
- Change only the body of `total_weight`.
- Do not edit, reformat, sort or regenerate the `ROWS` table. It must keep all of its rows.
- Use the standard library only.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: the changed lines (file:line) and the check result. Do not paste the table.
