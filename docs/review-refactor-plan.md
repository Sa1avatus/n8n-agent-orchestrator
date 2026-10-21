# Code review and refactoring: plan for the mission before 1.0

Timing (operator 2026-10-21): this mission runs BEFORE the 1.0 release, after the 0.x feature missions (features-config, qa-smoke, providers-ui, the JSON configuration steps of docs/config-columns-plan.md) and before the Codex executor: 1.0 freezes the configuration and event formats, so every rename across a boundary and the removal of legacy columns must be done first.

The mission is written from this file. It is the checklist of what to look at, in which order and with which
tools. Tools marked (research) were confirmed by a web search on 2026-10-20; tools marked (known) come from
general practice and must be checked against their own documentation before use.

## 0. Rules of the whole mission

1. Audit first, change nothing; the operator approves the ranked list; only then refactor.
2. Pin behaviour before touching code: characterization tests that record what the code does today (research:
   practitioner guidance on refactoring with AI agents). A module that is too tangled to test is NOT touched: it goes
   on the list for a human.
3. Small verified steps: one seam or one extraction per task, tests after each step, public interfaces kept,
   cleanup never mixed with a functional change (research).
4. Profile before optimizing; every optimization carries a before and an after number (research: cProfile, py-spy,
   pytest-benchmark). No number, no optimization.
5. Comprehension debt is real (research: arXiv 2511.02922): every step explains WHY in its report, and the operator
   reads the audit list and the per-step diffs before they are accepted.
6. A rename or removal across a boundary (DB column, API field, env variable, route, n8n table) needs a one-version
   alias, a changelog line and the operator's approval (see `docs/naming.md`).
7. Static analysis output is a list of CANDIDATES, never an automatic delete (research: vulture misses implicit use).

## 1. Baseline (before anything changes), saved as `reports/review-baseline.json`

Lines and files per area; test count and coverage; ruff and mypy error counts per file; import time of the runner
and the dashboard (`python -X importtime`, known); cold start of the dashboard until the board shows figures;
latency and request count of the main board routes (summary, missions, queue, task list) and the number of
requests one board refresh makes; size of the static files served; database size and the biggest tables.

## 2. Read-only audit, with the tools

| Question | Python | JS / CSS / other |
|---|---|---|
| Dead code | vulture (research) | eslint `no-unused-vars` and unused exports; unused CSS classes by grep against the JS |
| Duplicates (same body, not only same name) | jscpd (known) | jscpd (known) |
| Complexity | radon cc and mi (research), ruff C901 with a threshold (research) | function length and nesting by a script |
| Import and dependency cycles | import-linter or pydeps (known) | madge `--circular` (research) |
| Loops and cycles at run time | every `while True`, retry loop and recursion: is there a bound, a backoff and an exit? (the RESUME loop and the interrupt loop seen in missions are the model) | polling timers: is the interval cleared on page change? |
| Hygiene | ruff T20 (print), ERA (commented-out code), ARG (unused arguments), PERF, BLE and E722 (swallowed errors) (known) | leftover `console.log`, TODO and FIXME list |
| Dependencies | pip-audit and deptry (unused or missing) (known) | npm audit and depcheck (known) |
| Secrets and risky calls | bandit and gitleaks (known); the rule never to read `.env` stays | - |
| Drift | env variables defined but never read; i18n keys unused or missing in ru or en; routes no UI calls; docs that name deleted files; columns and tables nothing reads | - |
| Operator scripts | `data-tables/ops`: last use of each file (log lines, git-less mtime), duplicates, the 57 files with the local model name | - |

Hard-coded values and names: the lists in `docs/naming.md` section 5 and in the memory note of this mission are the
starting point; every finding gets file:line, the proposed parameter name and where its value lives.

## 3. Performance pass (after the audit, numbers first)

- Profile the real hot paths with py-spy against a running dashboard during a board refresh (research) and cProfile
  for single functions; pytest-benchmark for pure hot functions with saved comparison runs (research).
- Known suspects from this project: the summary route (it took 70-100 s before the background figures), the number of
  requests per refresh (175 before the fix), per-mission queries that should be one query, cold-start figures showing
  a dash for 30-60 s, cache TTLs, the SQL behind each hot route (`EXPLAIN`), a sequence of about 40 static scripts,
  database bloat from idle transactions (fixed once: check it stayed fixed).
- Each change: before and after numbers in the report, tests green, behaviour unchanged.

## 4. Refactoring steps, in this order

1. Shared helpers: one module for the duplicated `esc`, `fetchJson`, `postJson`, `L` (JS) and `_iso`, `_read_json`,
   `_store_guard`, `_state_key_of`, the exception handlers (Python).
2. Hard-coded values become named settings (config, `hermes_config`, `roles_json` or a constants module).
3. Names brought to `docs/naming.md`, module by module.
4. Dead code, unused files and old stop-gaps removed after the operator approves each group (host watchers after the
   guards migration, `cloud_first_patch.py` after the roles setting, the repository workflow export if it is replaced).
5. Complexity: split the functions over the threshold, one per task, with characterization tests first.
6. Cycles broken by moving the shared piece down, never by a lazy import that hides the cycle.
7. Typing and lint debt: mypy and ruff error counts per file go down and never up; no check is loosened.

## 5. Report (Russian)

A before and after table (lines, files, duplicates, complexity average and maximum, dead code removed, ruff and mypy
counts, import time, cold start, route latencies and request counts, tests and coverage), the list of what was
deliberately NOT touched and why (no tests, boundary renames waiting for approval), and the rollback of each step.
