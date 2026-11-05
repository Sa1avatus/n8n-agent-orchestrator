# Task: raise the profile loader retry limit

The profile loader gives up too early when the profile store is briefly unavailable.

Change `RETRY_LIMIT` from 3 to 5 in the module under `src/` that defines the function `load_profile` and that `src/app/main.py` imports.

Rules:
- Do not change any other copy of `RETRY_LIMIT`. The repository has decoy copies in `src/archive/`, `src/tests/fixtures/` and `src/services/profile_loader_old.py`; leave them as they are.
- Do not change any function other than the value of `RETRY_LIMIT`.
- Find the file with search (grep for `def load_profile(`), not by opening every file.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: name the file you changed (path and line) and the one line you changed.
