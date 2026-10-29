# Facts: the QA request flow (first section of the facts file)

Task T001A of mission `ahawr-qa-agent`. All file:line references below were read and
verified directly; nothing is quoted from memory.

## QA request flow

### 1. Request creation and storage

- **Creation**: `POST /v1/qa/requests` is defined at `api.py:516-526`. It builds a
  `QaRequestBody` (`api.py:64-77`: `mission_id`, `working_directory`, `mode`, `nonce`,
  optional `layers`; the `layers` validator is at `api.py:71-77` and rejects names not in
  `QA_LAYERS`). The route calls
  `QaRequestStore.create(mission_id, working_directory, mode, nonce, layers)`
  (`qa_requests.py:107-138`): it rejects an unknown mode (`REQUEST_MODES = ("final", "ui_tasks")`,
  `qa_requests.py:30`) and unknown layers, is **idempotent by `mission_id` + `nonce`**
  (returns the existing request, `qa_requests.py:122-124`), assigns a uuid4 id
  (`qa_requests.py:126`), trims the store to `QA_MAX_STORED_REQUESTS = 200`
  (`qa_requests.py:25`, `_trim` at `qa_requests.py:186-195`), and saves atomically to a
  single JSON file (`_save` at `qa_requests.py:203-207`).
- **Store location**: the store is created in the app lifespan at
  `api.py:236` — `QaRequestStore(str(settings.data_dir / "qa_requests.json"))`.
- **Shape and state machine**: `QaRequest` dataclass at `qa_requests.py:45-58`;
  legal transitions are `QA_TRANSITIONS = frozenset({("queued","running"), ("queued","timeout"),
  ("running","done"), ("running","error"), ("running","timeout")}` (`qa_requests.py:32-38`),
  applied by `transition()` (`qa_requests.py:67-77`). Terminal states: `TERMINAL_STATES`
  (`qa_requests.py:31`).
- **Expire rules**: `_is_expired` (`qa_requests.py:79-88`) — a `queued` request times out
  after `QA_CLAIM_WAIT_MINUTES = 5` (`qa_requests.py:23`), a `running` request after
  `QA_RUN_BOUND_MINUTES = QA_MAX_MINUTES` (`qa_requests.py:24`); `_expire` transitions
  expired requests to `timeout` on every read (`qa_requests.py:178-184`).

### 2. The existing layers and how a layer is selected

- **Layer vocabulary**: `QA_LAYERS = ("regression", "api", "ui")` and
  `QA_DEFAULT_LAYERS = ("ui",)` at `roles_config.py:21-22`.
- **Request-level selection**: `request_layers(request)` (`qa_requests.py:61-64`) returns the
  request's `layers` if set, else the default `("ui",)`.
- **Dispatch**: the worker builds one runner per layer in `_layer_runners`
  (`qa_worker.py:336-344`):
  - `ui` → `python -m claude_runner.qa run` (`qa_worker.py:339`),
  - `regression` → `python -m claude_runner.qa regression --workdir <wdir>`
    (`qa_worker.py:340-342`),
  - `api` → `python -m claude_runner.qa api` (`qa_worker.py:343`).
  Each is produced by `_layer_command` (`qa_worker.py:302-333`); the ui layer first checks
  `early_verdict` and returns it without running the browser (`qa_worker.py:306-309`).
- **Bounded execution**: `run_layers` (`qa_worker.py:401-410`) validates the requested layer
  names, iterates in the fixed order of `LAYER_TIMEOUT_SECONDS`
  (`qa_worker.py:52-56`: regression 25 min, api 5 min, ui 20 min), and runs each through
  `_run_layer` (`qa_worker.py:387-398`), which caps the layer at
  `min(LAYER_TIMEOUT_SECONDS[layer], remaining_seconds)` against the whole request deadline
  `RUN_TIMEOUT_SECONDS = QA_RUN_TIMEOUT_MINUTES * 60` (`qa_worker.py:48`); on timeout it
  reports a `timeout` verdict, on exception an `error` verdict (`qa_worker.py:394-397`).
- **The legacy path**: when the request has no `layers`, `_serve` uses `_execute`
  (`qa_worker.py:417-433`): one child process `python -m claude_runner.qa run`
  (`_run_child`, `qa_worker.py:347-364`), parsing its JSON verdict list, with the QA stack
  torn down on success, failure and timeout (`qa_worker.py:423-433`).
- **Early verdicts** (shared by both paths): `early_verdict` (`qa_worker.py:159-164`) calls
  `changed_files` (`qa_worker.py:135-140`, `git diff --name-only HEAD` plus `git ls-files
  --others`) and `has_ui_change` (`qa_worker.py:143-144`) against
  `QA_UI_FILE_PATTERN` (`qa_requests.py:26-29`); no UI change → `skipped`
  (`skipped_verdict`, `qa_worker.py:151-152`), a missing folder or a non-git folder →
  `blocked` (`blocked_verdict`, `qa_worker.py:147-148`, reasons `MISSING_FOLDER_REASON` /
  `GIT_FAILED_REASON` at `qa_worker.py:64-67`).

### 3. Where the result is built and reported

- **Serving one request**: `_serve` (`qa_worker.py:436-448`): `claim(request_id)` →
  `run_layers(list(layers), _layer_runners(request_row))` if layers are set, else
  `_execute` → `POST /v1/qa/requests/{request_id}/result` with `{"verdicts": ..., "error": ...}`,
  then a heartbeat write. The lock around serving is `_serve_locked` /
  `_serve_locked_safely` (`qa_worker.py:466-482`).
- **Verdict building**: `build_verdict` (`qa_runner.py:162-191`) produces the dict
  `{scenario, verdict, blocked_reason, failed_step, failed_reason, screenshots,
  console_errors, network_errors, minutes, cost_usd}`; the verdict word is `blocked` when
  `blocked_reason` is set, `fail` when there is a failed step or console/network errors, else
  `pass` (`qa_runner.py:174-179`). Browser scenario execution ends in `_settle`
  (`qa_runner.py:780-798`), which writes `verdict.json` into the run dir and returns the
  verdict dict.
- **Worker poll loop**: `main` (`qa_worker.py:508-542`) polls
  `list_queued()` (`GET /v1/qa/requests?status=queued`, `qa_worker.py:109-111`) in
  `for _ in range(MAX_POLL_CYCLES)` (`qa_worker.py:46`), exits on SIGINT/SIGTERM, and on
  consecutive poll errors backs off with `_backoff_seconds` (`qa_worker.py:495-496`,
  `min(POLL_SECONDS << (n-1), BACKOFF_MAX_SECONDS)`) until
  `MAX_CONSECUTIVE_ERRORS = 10` (`qa_worker.py:45`).

### 4. The gate verdict (workflow side)

- The gate is the n8n workflow's "QA Review" Code node, written by the idempotent patch
  `data-tables/ops/qa_workflow_patch.py` — the template `_QA_REVIEW_TEMPLATE` at
  `qa_workflow_patch.py:121-191`, with `QA_MAX_RETRIES = 2` (`qa_workflow_patch.py:115`) and
  `QA_REVIEW_JS` at `qa_workflow_patch.py:192`.
- Decision logic: `fail` verdicts → `failed()`, which increments a per-mission retry counter in
  workflow static data and returns outcome `fail` with `review_status: 'needs_changes'`
  (`qa_workflow_patch.py:158-173`); at the retry limit it returns `fail_at_limit`
  (`qa_workflow_patch.py:162-163`); `blocked` → `could_not_run` with the reason
  (`qa_workflow_patch.py:184-186`); all-pass or all-skipped → `skipped`
  (`qa_workflow_patch.py:189`) or `pass` ("QA passed", `qa_workflow_patch.py:190`); every
  infrastructure state (`queued`, `running`, `timeout`, `error`, unreachable runner) is
  `could_not_run` ("QA could not run ...", `qa_workflow_patch.py:175-182`).
- The fail-open/fail-closed table documenting this is `claude-runner/docs/qa-gate.md:85-96`
  (rows 1–10; only verdict `fail` sends a mission back).

### 5. The request as a run of role `qa`

- Constants: `QA_ROLE = "qa"`, `QA_PROVIDER = "qa"`, `QA_MODEL = "scripted"`
  (`qa_run_record.py:20-22`); the run id is `run_` + the request id
  (`run_id_of`, `qa_run_record.py:28-29`).
- **Opened at the claim**: route `POST /v1/qa/requests/{id}/claim` (`api.py:540-545`) calls
  `qa_run_record.start(manager(request), claimed)` (`api.py:544`). `start`
  (`qa_run_record.py:90-115`) creates the run with `role=qa`, `model=scripted`,
  `provider=qa`, input prompt "QA request <id> of mission <mission_id> (<mode>; <layers>)",
  opens the events file and adds a `prompt` event (`qa_run_record.py:112-113`). It never
  raises: a failure is logged and the QA request goes on (`qa_run_record.py:114-115`).
- **Closed at the result**: route `POST /v1/qa/requests/{id}/result` (`api.py:547-563`)
  calls `qa_run_record.finish(manager(request), finished)` (`api.py:562`). `finish`
  (`qa_run_record.py:118-145`) builds the one-paragraph output with `summarize`
  (`qa_run_record.py:72-87`: "QA verdicts: N pass, M fail, ..." plus the failed scenarios
  and the error), sets status `failed` when there is any `fail` verdict or an error, else
  `completed`, cost 0 (`qa_run_record.py:126-135`), then adds one `qa_scenario` event per
  verdict (`scenario_event`, `qa_run_record.py:56-69; called at qa_run_record.py:139-140`),
  a `qa_verdict` event (`qa_run_record.py:141-142`) and an `end` event
  (`qa_run_record.py:143`). It likewise never raises (`qa_run_record.py:144-145`).
- **When it started**: the CHANGELOG entry 0.11.600 (`CHANGELOG.md:18-24`, "QA requests as
  runs") records this behaviour; the commits are `ab25618 0.11.600` and `e3fbf5c 0.11.700`
  (per `git log` of `qa_run_record.py`). Note: the mission text in
  `_make_qa_agent_mission.py:19` says "since 0.11.500"; the 0.11.500 entry
  (`CHANGELOG.md:33-36`) is about providers and failover, so 0.11.600 is the version where
  the run record was introduced.
- **Test**: `claude-runner/tests/test_runs.py:537-554`
  (`test_qa_run_records_a_verdict_event_and_other_roles_do_not`) checks that the qa run gets
  exactly one `qa_verdict` event with role `qa` and status `completed`, and that a worker
  run gets none.

### 6. Where the QA settings live and how a flag is read

- **Role options**: `QA_OPTION_KEYS = COMMON_OPTION_KEYS | {"mode", "max_minutes",
  "max_cost_usd", "layers", "smoke"}` (`roles_config.py:17`);
  `QA_MODES = ("off", "final", "ui_tasks", "nightly")` (`roles_config.py:18`);
  `QA_DEFAULT_OPTIONS = {"mode": "off", "max_minutes": 20, "max_cost_usd": 1}`
  (`roles_config.py:19`); `QA_LAYERS` / `QA_DEFAULT_LAYERS` (`roles_config.py:21-22`).
- **Resolution**: `resolve()` (`roles_config.py:250-277`) merges
  `QA_DEFAULT_OPTIONS` in only for role `"qa"` (`roles_config.py:259-260`) and returns a
  `ResolvedRole` with `options` (`roles_config.py:271-277`). The options come from
  `roles_json` (global or per-mission, merged by `effective_roles`,
  `board_ready.py:350-354`).
- **The flag read by the scripted QA**: `smoke_enabled(options)` (`qa_worker.py:413-414`)
  is true when `options.get("smoke") is True` **and** the `claude_runner.qa_smoke` module
  is importable (`SMOKE_MODULE`, `qa_worker.py:57`). The options are read from the
  hermes_config roles_json by `_qa_options` (`qa.py:228-236`), which returns `{}` when the
  config is missing or unreadable — that is how the flag defaults to OFF. Used in the
  nightly path (`qa.py:198-210`).
- **Time bounds**: `QA_MAX_MINUTES = 20` (`config.py:190`), field `qa_max_minutes: int =
  QA_MAX_MINUTES` (`config.py:453`), env `CLAUDE_RUNNER_QA_MAX_MINUTES`
  (`config.py:563`); consumed as the running-request bound
  (`qa_requests.py:24`, `runs.py:746`).
- **Readiness check**: `check_qa_ready` (`board_ready.py:317-337`) resolves the qa role
  (`resolve(roles, QA_ROLE, 1, {})`), warns when `mode == "off"`, and errors when the mode
  is unknown or an environment probe problem exists; it is run from `_qa_item`
  (`board_ready.py:357-363`).
- **JS side**: the settings UI maps the keys in
  `board_edit_win.js:36` (`QA_LIMITS = [["mode", "qa_mode"], ["max_minutes", "qa_max_minutes"], ...]`).

## Open point

The mission `ahawr-qa-agent` was created from a finished mission, not from a QA-agent
source: `_make_qa_agent_mission.py:10` sets
`SOURCE = "ahawr-lessons-library"` ("a finished AHAWR mission: name, rules, criteria and
folder are replaced below"), and the mission text is written by the script's own
`RULES` / `ACCEPT` arrays (`_make_qa_agent_mission.py:35-52`), which are QA-agent
specific (qa_contract.py, qa_capture.py, qa_agent.py, the `qa_agent` role, the `agent`
layer, the flag `qa_agent` OFF by default).

However, the mission objective itself contains a sentence that belongs to the
**lessons library** feature, not to the QA agent: `_make_qa_agent_mission.py:19` —
"since 0.11.500 each request is recorded as a run of role 'qa'". The lessons feature
(what failed review and what fixed it, shown to the next tasks, with statistics) is the
objective of the source mission `ahawr-lessons-library`
(`_make_lessons_mission.py:7`, `data-tables/missions/mission_ahawr-lessons-library.csv`)
and is implemented in 0.11.200 (`CHANGELOG.md:84-94`: `board_lessons*.py`,
`lessons_patch.py`, `lessons_enabled` flag). Nothing in the QA request flow described above
involves lessons: the request lifecycle (`qa_requests.py`), the layers
(`qa_worker.py`), the verdict (`qa_runner.py`), the run record (`qa_run_record.py`) and
the gate (`qa_workflow_patch.py`) are all scripted QA.

So RULES 1 and the ACCEPTANCE CRITERIA of the mission do describe a lessons feature (as the
mission's own text does), which does not match the QA agent objective — a separate
thinking-model role that checks a mission's UI against an agreed design contract. The
plan follows the objective.

One more discrepancy worth recording: the mission text says the QA-run recording exists
"since 0.11.500", but `qa_run_record.py` was introduced in 0.11.600
(`ab25618 0.11.600: QA requests are runs of the dashboard`); the 0.11.500 entry
(`CHANGELOG.md:33-36`) covers providers and failover only.

## Stack, smoke and CLI

### 1. How `qa_stack` starts and stops the throwaway stack

`stack_up` (`qa_stack.py:124-151`) takes a fixture (`"default"` or `"empty"`,
`QA_FIXTURES` at `qa_stack.py:32`) and a mode from `STACK_MODES = ("docker",
"inprocess", "postgres")` (`qa_stack.py:33`).

- **docker**: builds a per-stack image from `claude-runner/Dockerfile`, creates a
  network and a volume, starts a `postgres:16-alpine` container on a free
  `127.0.0.1` port (`_start_postgres`, `qa_stack.py:212-230`), seeds it with
  `docs/board-schema.sql` plus the fixture SQL (`_seed_sql`, `qa_stack.py:399`),
  then starts the dashboard container on a second free port and sets
  `stack.dashboard_url = f"http://127.0.0.1:{host_port}"` (`_start_dashboard`,
  `qa_stack.py:233-251`; the URL is assigned at `qa_stack.py:251`).
- **inprocess**: spawns the dashboard as a child process with the fixture JSON
  and sets `dashboard_url` (`_inprocess_up`, `qa_stack.py:254-271`; the URL at
  `qa_stack.py:269`).
- **postgres**: needs the env vars `QA_PG_HOST` and `QA_PG_PASSWORD`
  (`QA_PG_HOST_ENV`/`QA_PG_PASSWORD_ENV`, `qa_stack.py:38-39`); it creates its
  own database `qa_<id>` on the shared server and spawns the dashboard with
  the DSN (`_postgres_up`, `qa_stack.py:274-299`; the URL at `qa_stack.py:291`).

The stack is identified by `run_id` (8 hex chars, `_RUN_ID_BYTES = 4`,
`qa_stack.py:62`). `stack_from_id` (`qa_stack.py:154-167`) reconstructs the
object names from the id (no state file). `stack_down` (`qa_stack.py:170-186`)
removes the seeded run dirs, kills the process in the inprocess/postgres modes,
or runs `docker rm`/`volume rm`/`network rm`/`rmi` in the docker mode; a failure
raises `QaStackError` (`qa_stack.py:77`).

The dashboard base URL is `http://127.0.0.1:<port>` in every mode: the docker
mode at `qa_stack.py:251`, inprocess at `qa_stack.py:269`, postgres at
`qa_stack.py:291`.

### 2. How `qa_smoke` uses Playwright

- **Import**: `run_smoke` imports `from playwright.sync_api import sync_playwright`
  at `qa_smoke.py:244` (inside the function, so the module imports without
  Playwright installed; a missing import returns a `blocked` verdict with
  `PLAYWRIGHT_HINT`, `qa_smoke.py:245-246`).
- **Browser launch**: `with sync_playwright() as playwright:` at
  `qa_smoke.py:249`; `browser = playwright.chromium.launch(headless=True)` at
  `qa_smoke.py:251`; `page = browser.new_page()` at
  `qa_smoke.py:254`; `browser.close()` in the `finally` block at
  `qa_smoke.py:260`. **No viewport is set** — the code calls
  `browser.new_page()` with no viewport argument and never calls
  `page.set_viewport`; the detector `qa_smoke_detect.py` measures overflow
  against a viewport width but does not configure one on the page.
- **Screenshots**: `_screenshot` (`qa_smoke.py:501-509`) calls
  `crawl.page.screenshot(path=str(crawl.run_dir / name))` at
  `qa_smoke.py:503`; the screenshot file is `finding-NNN.png` (name built at
  `qa_smoke.py:497`) and stored in the run dir; the path recorded in the
  verdict is `{run_dir.name}/{name}` (`qa_smoke.py:508`).
- **The QA run folder**: `run_smoke` creates the run dir via
  `_new_run_dir(runs_dir)` (`qa_smoke.py:247`, imported from `qa_runner`,
  `qa_runner.py:434-435`), which is `runs_dir / "qa-<8 hex chars>"`
  (`RUN_DIR_PREFIX = "qa-"`, `qa_runner.py:80`; `RUN_ID_BYTES = 4`,
  `qa_runner.py:70`). `runs_dir` defaults to `RUNS_DIR`
  (`qa_smoke.py:234`), which is `qa_runner.RUNS_DIR` = `qa_stack.QA_RUNS_DIR`
  (`qa_runner.py:78`, `qa_stack.py:54`: `QA_RUNS_DIR`, env-overridable via
  `QA_RUNS_ENV`, default `<claude-runner>/qa/runs`). The verdict JSON is
  written to `run_dir / "verdict.json"` (`qa_smoke.py:262`).
- **Crawl body**: `crawl` (`qa_smoke.py:266-280`) attaches event handlers
  (`_attach_events`, `qa_smoke.py:303-307`), then visits the seed start paths
  and the first `MISSION_PAGES` (3) mission pages, clicking up to
  `MAX_CLICKS_PER_PAGE` (25) controls per page, bounded by `MAX_PAGES` (40) and
  `MAX_MINUTES` (10) (`qa_smoke.py:57-61`).

### 3. Subcommands and argument parsing in `qa.py`

`build_parser` (`qa.py:46-108`) registers these subcommands under
`python -m claude_runner.qa`:

- `stack` → `up` (`--fixture`, choices `QA_FIXTURES`) / `down` (`stack_id`)
  (`qa.py:49-56`), handlers `_stack_up` (`qa.py:121-125`) and `_stack_down`
  (`qa.py:128-131`).
- `run` (`--tag`, `--scenario`, `--stack`) (`qa.py:57-61`), handler `_run`
  (`qa.py:134-141`).
- `api` (`--stack`) (`qa.py:62-66`), handler `_api` (`qa.py:152-155`).
- `nightly` (`qa.py:67-70`), handler `_nightly` (`qa.py:185-214`).
- `regression` (`--workdir`, required) (`qa.py:71-77`), handler `_regression`
  (`qa.py:358-365`).
- `baseline` (`project`, positional) (`qa.py:78-82`), handler `_baseline`
  (`qa.py:384-396`).
- `smoke` (`--url`, `--fixture`, `--lang`, `--allow-host`, `--max-pages`,
  `--max-clicks-per-page`, `--max-minutes`) (`qa.py:83-107`), handler `_smoke`
  (`qa.py:239-262`).

`main` (`qa.py:111-118`) parses args, calls the handler, and maps
`QaStackError`/`ValueError` to a non-zero exit. The `--stack` option (choice of
`STACK_MODES`) is added by `_add_stack_option` (`qa.py:144-149`); the mode is
resolved by `_stack_mode` (`qa.py:177-182`), falling back to env `QA_STACK_MODE`
(`STACK_MODE_ENV`, `qa.py:33`) then `DEFAULT_STACK_MODE = "docker"`
(`qa.py:34`).

### 4. What the docs say about the container, layers and acceptance

- **Container** (`claude-runner/docs/qa-container.md`): the `ahawr-qa` service
  (profile `qa-container`) runs QA in a Linux container with Playwright,
  pytest, ruff, mypy and node; the host worker polls the runner queue from
  inside the compose network at `http://claude-runner:8700`
  (`qa-container.md:22-27`). The rights table (`qa-container.md:34-44`) gives
  read-write only to `/qa-runs` (verdicts, screenshots, logs) and the heartbeat
  dir; the Docker socket is never mounted (`qa-container.md:49-57`). The
  `postgres` mode (2026-10-26, the mode of `ahawr-qa`) uses the shared
  `qa-postgres` service, no Docker access (`qa-container.md:72-86`).
- **Layers** (`claude-runner/docs/qa-layers.md:150-167`): all commands run
  inside the QA container through `python -m claude_runner.qa`; the layers are
  `regression`, `api`, `ui` (and `smoke` is a scenario-less layer, off by
  default). The rule table in `qa-acceptance.md:43-62` maps each layer outcome
  to its effect on the mission; only `fail` returns the mission for changes.
- **Acceptance** (`claude-runner/docs/qa-acceptance.md`): QA has four modes
  (`off`, `final`, `ui_tasks`, `nightly`) set by `mode` in the `qa` options of
  `roles_json` (`qa-acceptance.md:12-22`); it may only touch the throwaway
  stack (`qa-acceptance.md:36-42`); verdicts are `pass`/`fail`/`blocked`
  (`qa-acceptance.md:44-50`); a `fail` becomes a review with
  `review_status = needs_changes` and the failing step as `next_task`
  (`qa-acceptance.md:52-64`); per-run artefacts (verdict JSON, screenshots,
  console and network errors) are kept 30 days (`qa-acceptance.md:77-81`).

### 5. Is Playwright already a dependency, and where is it declared

Playwright **is** a declared dependency: it is in the `qa` optional-dependencies
extra of `claude-runner/pyproject.toml` — `qa = ["pyyaml>=6,<7",
"playwright>=1.45,<2"]` (`pyproject.toml:21`). It is **not** in the core
`dependencies` list (`pyproject.toml:10-17`) nor in the `dev` extra
(`pyproject.toml:20`). The mypy override ignores its imports
(`pyproject.toml:54-56`).

Note: `claude-runner/docs/qa-acceptance.md:373-386` states Playwright is "not
declared in any pyproject extra" and that it must be added manually — that is
stale. The current `pyproject.toml` does declare it under the `qa` extra
(`pyproject.toml:21`). The `PLAYWRIGHT_HINT` in `qa_runner.py:81-84` tells the
user to "install the qa extra with `pip install -e '.[qa]'`", which is
consistent with the declared extra.

## What was not verified

- The live mission row of `ahawr-qa-agent` (its RULES/ACCEPTANCE arrays as stored in the
  n8n data table): the dashboard on port 8701 is unreachable from this host and there is no
  `mission_ahawr-qa-agent.csv` on disk (`data-tables/missions/` and `archive/`). The
  creation script `_make_qa_agent_mission.py` was read in full (96 lines) and is the only
  available source of the mission text; it posts the `RULES` and `ACCEPT` arrays above
  through `POST /api/board/missions/create` + `POST /api/board/missions/<id>`
  (`_make_qa_agent_mission.py:70-96`).
- No container was recreated, no n8n import or docker command was run (not possible here:
  no docker access; the dashboard is down).
- No source file was changed; only this facts file was created.

## Facts: roles, role options, role tools and the QA changelog (T001C)

Task T001C of mission `ahawr-qa-agent`. Every file:line below was read directly; the
option-key lines were additionally re-verified with `grep -n` in both source files.

### 1. The known role names

- Python: `KNOWN_ROLES = frozenset({"architect", "worker", "reviewer", "service", "qa",
  "report"})` (`roles_config.py:15`).
- Runner profiles: `ROLES = ("architect", "worker", "reviewer", "qa", "generic")`
  (`config.py:18`). There is no `service` or `report` profile; `normalize_role` maps an
  empty or unknown role to `"generic"` (`runs.py:263-265`).
- Docs: `docs/roles-config.md:110-111` — "The role names are `architect`, `worker`,
  `reviewer`, `service`, `qa` and `report`; more can be added without a schema change."

### 2. The role option keys and where they are defined

The option keys live under the reserved key `options` of a role entry in `roles_json`.
There is no key named `thinking`: the option is `think` (`roles_config.py:16`).
`provider` and `model` are not option keys: they come from the role's model string
(`provider/model`, split at the first slash, `roles_config.py:39-41`) and are fields of
the resolved role and of the run request (below).

- Python (`roles_config.py`):
  - `COMMON_OPTION_KEYS = frozenset({"fresh", "think", "max_polls", "poll_seconds",
    "max_retries"})` — `roles_config.py:16`;
  - `QA_OPTION_KEYS = COMMON_OPTION_KEYS | {"mode", "max_minutes", "max_cost_usd",
    "layers", "smoke"}` — `roles_config.py:17`;
  - `QA_MODES = ("off", "final", "ui_tasks", "nightly")` — `roles_config.py:18`;
  - `QA_DEFAULT_OPTIONS = {"mode": "off", "max_minutes": 20, "max_cost_usd": 1}` —
    `roles_config.py:19`;
  - `QA_LIMIT_KEYS = ("max_minutes", "max_cost_usd")` — `roles_config.py:20`;
  - validation: `_check_options` allows `QA_OPTION_KEYS` only for role `qa`, else
    `COMMON_OPTION_KEYS` (`roles_config.py:148-155`); `_check_qa_options` checks `mode`
    against `QA_MODES` and both limits against a positive finite number, `smoke` against
    a boolean, `layers` against a list of `QA_LAYERS` (`roles_config.py:158-171`);
  - `resolve()` merges the defaults only for role `"qa"` (`roles_config.py:259-260`) and
    returns `ResolvedRole(provider, model, think, fresh, options)` (`roles_config.py:222-228,
    250-277`).
- JS (`board_roles.js`), the pure core mirrored from `roles_config.py`:
  - `COMMON_OPTION_KEYS = ["fresh", "think", "max_polls", "poll_seconds", "max_retries"]`
    — `board_roles.js:10`;
  - `QA_OPTION_KEYS = [...COMMON_OPTION_KEYS, "mode", "max_minutes", "max_cost_usd",
    "layers"]` — `board_roles.js:11` (no `smoke` in the core validator; `smoke` is added
    in the settable list below);
  - `QA_LAYERS` / `QA_DEFAULT_LAYERS` — `board_roles.js:13-14`;
  - `QA_SETTABLE_OPTIONS = ["mode", "max_minutes", "max_cost_usd", "smoke"]` —
    `board_roles.js:425`, used by `qaOptionsFrom` (`board_roles.js:427-445`);
  - `QA_DEFAULT_OPTIONS = { mode: "off", max_minutes: 20, max_cost_usd: 1 }` and
    `QA_MODES = ["off", "final", "ui_tasks", "nightly"]` — `board_roles.js:448-449`;
  - `resolveRole(roles, role, attempt, legacy, facts)` returns
    `{ provider, model, think, fresh, options }` — `board_roles.js:382-401`.
- Docs: `docs/roles-config.md:100-101` — "all roles: `fresh`, `think`, `max_polls`,
  `poll_seconds`, `max_retries`; `qa` only: also `mode`, `max_minutes`, `max_cost_usd`";
  validation rules at `docs/roles-config.md:138-155`; the QA defaults and limits at
  `docs/roles-config.md:192-197` ("`max_minutes`: default `20`; `max_cost_usd`: default
  `1`. Both must be positive numbers.").

### 3. How Python and JS share tests/roles_cases.json

`tests/roles_cases.json` (1312 lines) holds the shared case bundles `providers`,
`validation` and `resolve`:

- Python: `test_roles_config.py:23-26` loads it (`CASES`, `PROVIDERS = CASES["providers"]`),
  `test_shared_validation_case` runs the `validation` bundle against
  `validate_roles_json` (`test_roles_config.py:62-66`), and `test_shared_resolve_case`
  runs the `resolve` bundle against `resolve()` (`test_roles_config.py:69-81`).
- JS: `board_roles.test.js:1-10` — "Runs the shared roles_cases.json against
  board_roles.js; mirrors test_roles_config.py"; it requires `board_roles.js` (line 7),
  reads the same file (line 9) and runs the `validation` (`board_roles.test.js:49-54`)
  and `resolve` (`board_roles.test.js:57-69`) bundles against `validateRolesJson` /
  `resolveRole`.
- The 0.11.400 entry records the sharing: `CHANGELOG.md:69` — "Tests: ...,
  `roles_cases.json` (shared by Python and JS)".

### 4. Where a role's allowed tools are defined

The tool policy per role is a `RoleProfile` (`config.py:135-147`): `permission_mode`,
`allowed_tools`, `disallowed_tools`, `append_system_prompt`, `max_turns`, `tools`
(`--tools`, the built-in tool set) and `add_dirs` (`--add-dir`).

- Defaults (`config.py:339-349`):
  - `architect`: `"dontAsk"`, disallowed `_READ_ONLY_DENY + _SECRET_DENY`;
  - `worker`: `"bypassPermissions"`, disallowed `_SECRET_DENY + _GIT_DENY`;
  - `reviewer`: `"dontAsk"`, allowed `_REVIEWER_BASH_ALLOW`, disallowed
    `_READ_ONLY_DENY + _SECRET_DENY + _REVIEWER_BASH_DENY`;
  - `qa`: `"dontAsk"`, disallowed `_READ_ONLY_DENY + _SECRET_DENY`;
  - `generic`: `"dontAsk"`, disallowed `_READ_ONLY_DENY + _SECRET_DENY`.
- The deny/allow lists: `_READ_ONLY_DENY = ["Edit", "Write", "NotebookEdit"]`
  (`config.py:56`), `_SECRET_DENY` (Read of `.env` files, `config.py:70`), `_GIT_DENY`
  (config.py:76), `_REVIEWER_READ_CMDS` (`config.py:91-111`), `_REVIEWER_GIT_READ`
  (`config.py:112-124`), `_REVIEWER_BASH_ALLOW` (`config.py:125-131`) and
  `_REVIEWER_BASH_DENY` (`config.py:132`).
- Per-role environment override: `CLAUDE_RUNNER_<ROLE>_{PERMISSION_MODE, ALLOWED_TOOLS,
  DISALLOWED_TOOLS, APPEND_SYSTEM_PROMPT, MAX_TURNS, TOOLS, ADD_DIRS}`
  (`config.py:502-520`); `settings.profile(role)` falls back to `"generic"`
  (`config.py:594-595`) and is used by the Run Manager when it builds the child process
  (`runs.py:445`).

### 5. The POST /v1/runs body fields for role and prompt

- `RunBody` (`api.py:151-163`): `input` (the prompt), `model`, `provider`, `session_id`,
  `working_directory`, `role`, `think_policy`, `think_policy_from_task`,
  `think_off_after_seconds`, `think_off_after_steps`.
- Route `start_run` (`api.py:379-402`): `role=body.role or x_ahawr_role` (header fallback),
  `model=body.model`, `provider=body.provider`; it calls `manager(request).start`.
- `RunManager.start` (`runs.py:304-348`): `model = request.model.strip() or
  settings.default_model` (`runs.py:308`), `role = self.normalize_role(request.role)`
  (`runs.py:309`), `provider = self._provider(request.provider.strip())`
  (`runs.py:319`; `_provider` at `runs.py:267-271` checks the env config first, then the
  provider database via `extra_provider`), then `store.create_run` stores `role`,
  `model`, `provider` and the `think_*` values (`runs.py:326-342`) and `_launch`
  (`runs.py:345`, `_launch` at `runs.py:378-397`).
- Thinking resolution at launch: `profile = self.settings.profile(role)`
  (`runs.py:445`); for non-local providers with `settings.cloud_think` on,
  `cloud_think.resolve_cloud_think(...)` sets the per-attempt thinking variant/mode
  (`runs.py:453-467`).
- QA bound: `_qa_overdue` stops a qa run once
  `time.monotonic() - started > settings.qa_max_minutes * 60` (`runs.py:742-746`);
  `qa_max_minutes` defaults to `QA_MAX_MINUTES = 20` (`config.py:190, 453`) with env
  `CLAUDE_RUNNER_QA_MAX_MINUTES` (`config.py:563`).

### 6. The 0.11.400 CHANGELOG entries for the Providers and Guards pages

The entry (`CHANGELOG.md:49-69`, header `CHANGELOG.md:49`, title `CHANGELOG.md:51`
"Small step: the Providers and Guards pages, the Worker escalation and fixes.") reads:

- "Providers page (`board_providers_view.js`, `board_providers_form.js`, `board.css`):
  look of variant A (cards: Connection, API key, Models), a Delete provider button
  (refused with the reason while a role uses the provider; the notice is shown on the
  page, it was hidden before), and the page opens from a Providers tab next to Guards
  in the dashboard shell (first click after a fresh load works)."
  (`CHANGELOG.md:53-55`)
- "Fetch models (`GET /api/board/providers/{name}/available-models`): lists what the
  provider itself offers (`/models`, `/v1/models` for the Anthropic type), with the
  stored key; the page filters by name and "Free only" (OpenRouter prices, `:free`) and
  adds the picked models with their prices. LiteLLM with a wildcard route lists its own
  catalogue, not the local models: list the models through the llama-server provider
  instead." (`CHANGELOG.md:56-59`)
- "Guards page, list and detail (`board_guards.js`, `guards.py`,
  `PUT /api/guards/{name}`): per guard an on/off switch, the poll interval (1 to 86400 s)
  and the health window (1h, 24h, 7d) with ok share, checks and errors in the window.
  Changes act at once (the task of the guard starts, stops or restarts) and are kept in
  `guards_overrides.json` of the state directory. Flag `DASHBOARD_GUARDS_EDIT` (OFF by
  default; the master switch and dry run stay stronger)." (`CHANGELOG.md:60-63`)

The same entry also records the escalation block `from_attempt` (`CHANGELOG.md:64-66`)
and that a run starting or ending no longer pulls the reader off the Providers, Guards or
Missions pages (`CHANGELOG.md:67-68`). QA context from the changelog: 0.11.600
(`CHANGELOG.md:18-31`, "QA requests as runs") and 0.11.500 (`CHANGELOG.md:33-47`,
providers from the database and failover).

### Verification run

`grep -n` of the cited option keys (ran 2026-10-28):

- `roles_config.py`:
  ```
  16:COMMON_OPTION_KEYS = frozenset({"fresh", "think", "max_polls", "poll_seconds", "max_retries"})
  17:QA_OPTION_KEYS = COMMON_OPTION_KEYS | {"mode", "max_minutes", "max_cost_usd", "layers", "smoke"}
  19:QA_DEFAULT_OPTIONS: dict[str, Any] = {"mode": "off", "max_minutes": 20, "max_cost_usd": 1}
  20:QA_LIMIT_KEYS = ("max_minutes", "max_cost_usd")
  ```
- `board_roles.js`:
  ```
  10:const COMMON_OPTION_KEYS = ["fresh", "think", "max_polls", "poll_seconds", "max_retries"];
  11:const QA_OPTION_KEYS = [...COMMON_OPTION_KEYS, "mode", "max_minutes", "max_cost_usd", "layers"];
  425:const QA_SETTABLE_OPTIONS = ["mode", "max_minutes", "max_cost_usd", "smoke"];
  448:const QA_DEFAULT_OPTIONS = { mode: "off", max_minutes: 20, max_cost_usd: 1 };
  ```

All cited lines match the grep output. No test was run for this task (it only asks for
facts and their verification by grep); no source file was changed.

## Facts: Providers page UI strings (T015A)

Task T015A of mission `ahawr-qa-agent`. The strings below are the English fallbacks
quoted verbatim from the source (the second argument of `L`/`ui.L`); each was
matched with `grep -n` in `claude-runner/src/claude_runner` (every string returns a
match, see the verification block at the end). They back the items C1..C11 of the
contract `claude-runner/qa/contracts/ahawr-providers-ui/contract.md`.

- The page opens from the Providers tab of the dashboard shell: the shell is served at `/`
  (`dashboard.py:251`); with the hash `#board` the board tab is shown
  (`dashboard.html:1258`), and clicking the Providers tab (`tabprov`,
  `dashboard.html:1322-1329`) sets the board frame to `/static/board.html#board`
  (`dashboard.html:1081`) and calls `BoardProvidersView.open()` to render the
  master-detail page into `#board`. The tab label is `prov.open` = "Providers"
  (`board_i18n.js:518`, `board_i18n_en.json:prov.open`).
- The agreed viewport for the `ahawr-providers-ui` contract is `1280x800` (the smoke
  crawler itself sets no viewport — `qa_smoke.py:254`).

### Strings and their source (verified with `grep -n`)

Master list / header (`board_providers_view.js`):

- "Providers" — `board_providers_view.js:278` (`prov.title`)
- "Back to missions" — `board_providers_view.js:275` (`prov.page_close`)
- "New provider" — `board_providers_view.js:225` (`prov.form.new`)
- "No providers yet." — `board_providers_view.js:229` (`prov.empty`)
- "key set" — `board_providers_view.js:127` (`prov.key_short`)
- "no key" — `board_providers_view.js:95`, `board_providers_view.js:127`,
  `board_providers_view.js:203` (`prov.key_no`)
- "off" — `board_providers_view.js:130` (`prov.off`)
- "Import from the environment" — `board_providers_view.js:218` (`prov.import_title`)
- "Create" — `board_providers_view.js:199` (`prov.create`)
- "Nothing to import." — `board_providers_view.js:216` (`prov.import_none`)
- "Keys are never shown: only whether a key is set and when it was set." —
  `board_providers_view.js:279` (`prov.hint`)

Detail / form (`board_providers_form.js`):

- "Connection" — `board_providers_form.js:509` (`prov.connection`)
- "API key" — `board_providers_form.js:178` (`prov.key.title`)
- "New key" — `board_providers_form.js:180` (`prov.key.field`)
- "Save key" — `board_providers_form.js:175` (`prov.key.save`)
- "Models" — `board_providers_form.js:417` (`prov.models.title`)
- "No models yet." — `board_providers_form.js:421` (`prov.models.none`)
- "Fetch models" — `board_providers_form.js:388` (`prov.fetch`)
- "Filter by name" — `board_providers_form.js:349` (`prov.fetch.filter`)
- "Free only" — `board_providers_form.js:379` (`prov.fetch.free`)
- "Select shown" — `board_providers_form.js:354` (`prov.fetch.all`)
- "Add selected ({n})" — `board_providers_form.js:362` (`prov.fetch.add`)
- "Delete provider" — `board_providers_form.js:519` (`prov.delete`)
- "Delete provider {name}? Its models and its stored key are removed. This cannot be undone."
  — `board_providers_form.js:464` (`prov.delete_confirm`)
- "Provider {name} deleted." — `board_providers_form.js:472` (`prov.deleted`)
- "key set, since {when}" — `board_providers_view.js:94` (`prov.key_yes`, rendered by
  `keyText` and shown in the API key card)
- Form row labels: "Name", "Label", "API type", "Base URL", "Enabled", "Notes" —
  `board_providers_form.js:498-504` (`prov.form.name`, `prov.form.label`, `prov.form.api`,
  `prov.form.url`, `prov.form.enabled`, `prov.form.notes`)
- "Save" — `board_providers_form.js:493` (`prov.form.save`); "Close" —
  `board_providers_form.js:495` (`prov.close`)

### API answers the items are checked against

- `GET /api/board/providers` returns `{"providers": [...]}` (`board_providers_api.py:455-458`);
  an empty database answers `{"providers": []}`.
- `GET /api/board/providers/import-from-env` returns `{"proposals": [...]}`
  (`board_providers_api.py:460-463`); an empty environment answers `{"proposals": []}`.
- `GET /api/board/providers/{name}/available-models` returns the fetched model list
  (`board_providers_api.py:565-568`), driven by the "Fetch models" button
  (`board_providers_form.js:388`).

### Verification (`grep -n` of each quoted UI string in `claude-runner/src/claude_runner`)

- "Back to missions" → 3 matches (`board_providers_view.js:275`, i18n en/ru)
- "Providers" → matches (`board_providers_view.js:278`, `board_i18n.js:518/519`, `dashboard.html:1083`)
- "key set" → 7 matches (`board_providers_view.js:127` among them)
- "no key" → 10 matches (`board_providers_view.js:95/127/203` among them)
- "No providers yet." → 3 matches (`board_providers_view.js:229` among them)
- "New provider" → 5 matches (`board_providers_view.js:225`, `board_providers_form.js:529` among them)
- "Import from the environment" → 3 matches (`board_providers_view.js:218` among them)
- "Create" → 9 matches (`board_providers_view.js:199` among them)
- "Nothing to import." → 3 matches (`board_providers_view.js:216` among them)
- "Connection" → 6 matches (`board_providers_form.js:509` among them)
- "Name" → matches (`board_providers_form.js:498` among them)
- "Label" → 22 matches (`board_providers_form.js:499` among them)
- "API type" → 13 matches (`board_providers_form.js:500` among them)
- "Base URL" → 5 matches (`board_providers_form.js:502` among them)
- "Enabled" → 9 matches (`board_providers_form.js:503` among them)
- "Notes" → 15 matches (`board_providers_form.js:504` among them)
- "Save" → 87 matches (`board_providers_form.js:493` among them)
- "API key" → 10 matches (`board_providers_form.js:178` among them)
- "New key" → 3 matches (`board_providers_form.js:180` among them)
- "Save key" → 3 matches (`board_providers_form.js:175` among them)
- "Models" → 10 matches (`board_providers_form.js:417` among them)
- "No models yet." → 3 matches (`board_providers_form.js:421` among them)
- "Fetch models" → 3 matches (`board_providers_form.js:388` among them)
- "Filter by name" → 3 matches (`board_providers_form.js:349` among them)
- "Free only" → 3 matches (`board_providers_form.js:379` among them)
- "Select shown" → 3 matches (`board_providers_form.js:354` among them)
- "Add selected ({n})" → 3 matches (`board_providers_form.js:362` among them)
- "Delete provider" → 5 matches (`board_providers_form.js:519` among them)
- "Delete provider {name}? Its models and its stored key are removed. This cannot be undone."
  → 3 matches (`board_providers_form.js:464` among them)
- "Provider {name} deleted." → 3 matches (`board_providers_form.js:472` among them)
- "key set, since {when}" → 3 matches (`board_providers_view.js:94` among them)
- "Keys are never shown: only whether a key is set and when it was set" → 3 matches
  (`board_providers_view.js:279` among them)

The contract file itself was checked with the project parser `qa_contract.parse_contract`
/ `validate_contract`: the title, screen, route (`route /providers`), viewport
(`1280x800`), layout and reference image (`providers-reference.png`) parse as expected,
and items C1..C11 parse as a sequential run; the only validation note is the absent
reference image, which is expected because it is a stand-in to be captured.

## Facts: Guards page UI strings (T016A)

Task T016A of mission `ahawr-qa-agent`. The strings below are the English fallbacks
quoted verbatim from the source (the second argument of the `ctx.t`/`guardT` calls,
or the `GUARD_TEXT` map); each was matched with `grep -n` in
`claude-runner/src/claude_runner` (every string returns a match, see the verification
block at the end). They back the items C1..C12 of the contract
`claude-runner/qa/contracts/ahawr-guards-detail/contract.md`.

- The page opens from the Guards tab of the dashboard shell: the shell is served at `/`
  (`dashboard.py:251`); with the hash `#guards` the Guards tab is shown
  (`dashboard.html:1258`), and the Guards tab button (`tabguards`,
  `dashboard.html:1294-1298`) sets the hash `#guards` and calls `syncTab()` to render
  the guard pane (`#guardspane`) into which `BoardGuards.mount(pane, ...)` mounts
  `board_guards.js` (`dashboard.html:1272`). The tab label is `guardT("title", "Guards")` =
  "Guards" (`board_i18n.js:403`, `board_i18n_en.json:49`). The agreed viewport is
  `1280x800`.
- All texts of the tab come from the board i18n keys (`guards.*` in
  `board_i18n_en.json`/`board_i18n_ru.json`, served at `GET /api/board/i18n`) with the
  `set.*` and `tab.view.detail` keys bundled in the `GUARD_TEXT` map of
  `dashboard.html` (`GUARD_TEXT.en`, lines 1114-1140); `tests/board_i18n.test.js:140-141`
  and `tests/test_board_js_static.py:171-179` enforce parity between the bundled table,
  the English JSON and the Russian JSON.

### Strings and their source (verified with `grep -n`)

Page header (`board_guards.js:54-58`, `header()`):

- "guards on" — `board_guards.js:56` (`tab.on`; key `guards.tab.on`, `board_i18n.js:417`)
- "guards off" — `board_guards.js:56` (`tab.off`; key `guards.tab.off`, `board_i18n.js:418`)
- "dry run" — `board_guards.js:46` (`dry`; key `guards.dry`, `board_i18n.js:412`,
  `dashboard.html:1112`)
- "ok" — `board_guards.js:58` (key `guards.ok`, `board_i18n.js:407`)
- "warning" — `board_guards.js:58` (key `guards.warning`, `board_i18n.js:408`)
- "error" — `board_guards.js:58` (key `guards.error`, `board_i18n.js:409`)
- "disabled" — `board_guards.js:58` (`tab.disabled`; key `guards.tab.disabled`,
  `board_i18n.js:419`)

View tabs (`board_guards.js:182`, `viewHtml()` → `VIEWS = ["detail","cards","flow","lanes"]`):

- "Detail" — `dashboard.html:1130` (`GUARD_TEXT.en "tab.view.detail":"Detail"`)
- "Cards" — `board_i18n.js:428` (`guards.tab.view.cards`)
- "Flow" — `board_i18n.js:429` (`guards.tab.view.flow`)
- "Timelines" — `board_i18n.js:430` (`guards.tab.view.lanes`)

Left list (`board_guards.js:148`, `detailView()` — guard buttons):

- "—" — `board_guards.js:45,146` (`none`; key `guards.none`, `board_i18n.js:410`,
  `dashboard.html:1120`)

Detail header (`board_guards.js:150-156`):

- "dry run" — `board_guards.js:46` (see above)
- "last" — `board_guards.js:66,155` (`guardT("last",...)`; `GUARD_TEXT.en` key `last`,
  `board_i18n.js:405`, `dashboard.html:1118`)
- "ago" — `board_guards.js:64,152` (`tab.ago`; key `guards.tab.ago`,
  `board_i18n.js:421`)

Guard descriptions (`board_guards.js:17-23`, the `GUARD_TEXT` map):

- "Restarts the mission queue when it stops" — `board_guards.js:18` (queue_restart)
- "Watches the host watchers" — `board_guards.js:19` (host_heartbeat)
- "Forwards notices to Telegram" — `board_guards.js:21` (tg_relay)
- "Posts mission start and end notices" — `board_guards.js:22` (mission_notice)
- "Stops idle runs after a timeout" — `board_guards.js:23` (idle)

Settings card (`board_guards.js:124-134`, `settingsCard()`):

- "Settings" — `board_guards.js:127,130` (`set.title`; `GUARD_TEXT.en`,
  `dashboard.html:1131`)
- "Poll interval" — `board_guards.js:127,131` (`set.interval`; `GUARD_TEXT.en`,
  `dashboard.html:1132`)
- "s" — `board_guards.js:127,131` (fallback arg of `unit.s`; key `guards.unit.s` in
  `board_i18n.js:434`, `board_i18n_en.json:49`; not in `GUARD_TEXT.en` of `dashboard.html`)
- "Health window" — `board_guards.js:128,132` (`set.window`; `GUARD_TEXT.en`,
  `dashboard.html:1133`)
- "Editing is off (DASHBOARD_GUARDS_EDIT)." — `board_guards.js:128`
  (`set.off`; `GUARD_TEXT.en`, `dashboard.html:1139`)
- "Save" — `board_guards.js:133` (`set.save`; `GUARD_TEXT.en`, `dashboard.html:1134`)
- "1 hour" — `board_i18n.js:431` (`guards.tab.window.1h`; the 1h select option)
- "24 hours" — `board_i18n.js:432` (`guards.tab.window.24h`; the 24h select option)
- "7 days" — `board_i18n.js:433` (`guards.tab.window.7d`; the 7d select option)
- "The change was not saved" — `board_guards.js:203` (`set.failed`; key not in
  `board_i18n.js`, `GUARD_TEXT.en`, `dashboard.html:1140`)

Health card (`board_guards.js:136-141`, `healthCard()`):

- "Health" — `board_guards.js:141` (`set.health`; `GUARD_TEXT.en`,
  `dashboard.html:1135`)
- "checks" — `board_guards.js:139` (`set.checks`; `GUARD_TEXT.en`,
  `dashboard.html:1136`)
- "in" — `board_guards.js:139` (`set.in`; `GUARD_TEXT.en`, `dashboard.html:1137`)

Recent actions card (`board_guards.js:118-122,156`, `actionRows()` / `detailView()`):

- "Recent actions" — `board_guards.js:156` (`set.actions`; `GUARD_TEXT.en`,
  `dashboard.html:1138`)

Load error (`board_guards.js:228`):

- "Could not load the guards." — `board_guards.js:228` (`tab.error`; key
  `guards.tab.error`, `board_i18n.js:427`)

### API answers the items are checked against

- `GET /api/guards` returns `enabled` (master), `dry_run`, `running`, `guards[]` (each
  with `name`, `status`, `last_run`, `last_result`, `last_action`, `error_count`,
  `interval_s`, `health_window`, `health`, `enabled`, `dry_run`), `heartbeats[]` only
  when master is on, and `tab`/`history_summary`/`windows`/`edit` only when the tab flag
  is on (`dashboard.py:283-310`). It carries no environment values.
- `PUT /api/guards/{name}` — body fields `enabled`, `interval_s`, `health_window`;
  requires header `X-AHAWR-Dashboard: 1` and `DASHBOARD_GUARDS_EDIT=1`
  (`dashboard.py:312-321`); interval range 1 to 86400 (`guards.py:198-199`).
- `GET /api/guards/history?window=1h|24h|7d` — the checks of that window, thinned to
  200 per guard (`dashboard.py:338-347`).

### Verification (`grep -rn -F` of each quoted UI string in `claude-runner/src/claude_runner`)

Ran in the session that wrote this contract. Primary source is listed first;
some strings also appear in `board_i18n_en.json`/`board_i18n_ru.json` and
`dashboard.html` GUARD_TEXT en/ru.

- "guards on" → 3 matches (`board_guards.js:56`, `board_i18n.js:417`,
  `board_i18n_en.json:49`)
- "guards off" → 3 matches (`board_guards.js:56`, `board_i18n.js:418`,
  `board_i18n_en.json:49`)
- "dry run" → 10 matches (`board_guards.js:46,57`, `board_i18n.js:412`,
  `board_i18n_en.json:49`, `board_i18n_ru.json:49`, `dashboard.html:1112,1177`)
- "ok" → 892 matches (`board_guards.js:58`, `board_i18n.js:407`)
- "warning" → 142 matches (`board_guards.js:58`, `board_i18n.js:408`)
- "error" → 892 matches (`board_guards.js:58,139`, `board_i18n.js:409`)
- "disabled" → 92 matches (`board_guards.js:58`, `board_i18n.js:419`)
- "Detail" → 3 matches (`dashboard.html:1130`, also as a substring in
  `board_providers_view.js:233,251`)
- "Cards" → 10 matches (`board_i18n.js:428`, `board_i18n_en.json:49`,
  `board_view_kanban.js:31,53`)
- "Flow" → 4 matches (`board_guards.js:105`, `board_i18n.js:426,429`,
  `board_i18n_en.json:49`)
- "Timelines" → 2 matches (`board_i18n.js:430`, `board_i18n_en.json:49`)
- "Settings" → 44 matches (`board_guards.js:127,130`, `dashboard.html:1131`)
- "Poll interval" → 3 matches (`board_guards.js:127,131`, `dashboard.html:1132`)
- "s" → matches everywhere (single char); relevant sources: `board_guards.js:127,131`
  (fallback arg of `unit.s`), `board_i18n.js:434` / `board_i18n_en.json:49`
  (key `guards.unit.s`); not in `GUARD_TEXT.en` of `dashboard.html`
- "Health window" → 3 matches (`board_guards.js:128,132`, `dashboard.html:1133`)
- "Editing is off (DASHBOARD_GUARDS_EDIT)." → 2 matches
  (`board_guards.js:128`, `dashboard.html:1139`)
- "Save" → 87 matches (`board_guards.js:133`, `dashboard.html:1134`)
- "1 hour" → 2 matches (`board_i18n.js:431`, `board_i18n_en.json:49`)
- "24 hours" → 2 matches (`board_i18n.js:432`, `board_i18n_en.json:49`)
- "7 days" → 2 matches (`board_i18n.js:433`, `board_i18n_en.json:49`)
- "Health" → 5 matches (`board_guards.js:141`, `dashboard.html:1133,1135`,
  `board_guards.js:128,132` as part of "Health window")
- "checks" → 58 matches (`board_guards.js:139`, `board.css:601`)
- "in" → many matches (`board_guards.js:139`, `dashboard.html:1137`)
- "Recent actions" → 2 matches (`board_guards.js:156`, `dashboard.html:1138`)
- "Restarts the mission queue when it stops" → 1 match (`board_guards.js:18`)
- "Watches the host watchers" → 1 match (`board_guards.js:19`)
- "Forwards notices to Telegram" → 1 match (`board_guards.js:21`)
- "Posts mission start and end notices" → 1 match (`board_guards.js:22`)
- "Stops idle runs after a timeout" → 1 match (`board_guards.js:23`)
- "last" → 296 matches (`board_guards.js:66,155`, `board_i18n.js:405`,
  `dashboard.html:1118`)
- "ago" → 21 matches (`board_guards.js:64,152`, `board_i18n.js:421`)
- "—" (em dash) → matches (`board_guards.js:45,46,64,70,120,121,138,146,152`,
  `board_i18n.js:410`)
- "Could not load the guards." → 2 matches (`board_guards.js:228`,
  `board_i18n.js:427`)
- "The change was not saved" → 2 matches (`board_guards.js:203`, `dashboard.html:1140`)

The contract file was checked with the project parser `qa_contract.parse_contract` /
`validate_contract`: the title, screen, route (`route /#guards`), viewport
(`1280x800`), layout and reference image (`guards-reference.png`) parse as expected,
and items C1..C12 parse as a sequential run; the reference image exists in the
contract directory as a stand-in.
