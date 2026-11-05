# Task: fix `slugify` as the Reviewer asked

The previous attempt was rejected. The Reviewer wrote:

> "slugify in src/textutil.py still returns mixed case and keeps repeated hyphens. The result must be lowercase, every run of whitespace or hyphens must become one hyphen, and hyphens at the start or end must be removed. The function name and signature must stay the same."

Make `slugify(text: str) -> str` in `src/textutil.py` do exactly that, with these examples:
- `"Hello World"` gives `"hello-world"`
- `"  Hello   World  "` gives `"hello-world"`
- `"a--b"` gives `"a-b"`
- `"--x--"` gives `"x"`

Rules:
- Change only `src/textutil.py`.
- Keep the function name and its signature.
- Use the standard library only.

Proof: `python3 check.py` prints `PASS` and exits 0.

Final answer: the changed lines of `src/textutil.py` and the check result.
