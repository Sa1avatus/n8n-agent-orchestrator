# Task: fix `remove_item` and write a bilingual report

`src/inventory.py` keeps a stock dictionary. `remove_item` has two bugs:
- It drives the stock below zero when asked for more than is available. It must raise `ValueError` in that case and leave the stock unchanged.
- When the quantity reaches exactly zero, the item must be removed from the dictionary instead of being stored as `0`.

`remove_item` on an item that is not in the stock must also raise `ValueError`.

Rules:
- Change only `src/inventory.py`.
- Keep the signatures of `add_item` and `remove_item`.
- Use the standard library only.

Report: write the report to the file `report/worker_report.md` in this task directory. It must contain:
- a top-level heading `# Worker report`;
- a section `## Summary` written in English, describing the fix;
- a section `## Итоги` written in Russian (at least two sentences), describing the change to `remove_item` and the result of `python3 check.py`.

The final answer to the operator is in English and only needs the path of the report and the check result.

Proof: `python3 check.py` prints `PASS` and exits 0.
