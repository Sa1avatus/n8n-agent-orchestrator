# Task: move the request timeout out of the function into a named constant

`src/client.py` builds the options for every HTTP request. The timeout is the bare number `30` inside `request_options`. A reader cannot tell what `30` means, and the next person who changes it has to find it in the code.

Make the value a named constant and use the constant in the function.

Rules:
- Add a module-level constant `REQUEST_TIMEOUT_SECONDS` with the value `30`, typed as `int`.
- `request_options` must use `REQUEST_TIMEOUT_SECONDS`. The number `30` must not appear anywhere inside the body of `request_options`.
- The behaviour must not change: `request_options(2)` still returns `{"timeout": 30, "retries": 2}`.
- Change only `src/client.py`. Keep the function name and signature. Use the standard library only.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: the changed lines of `src/client.py` (file:line) and the check result.
