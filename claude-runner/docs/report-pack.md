# Report pack

## Causes and evidence

Sources and limits:

- Reviewer texts are the stored `output` of the Reviewer runs in `runner.sqlite` (table `runs`, read only; the same text as the `result` event in `events/<run_id>.jsonl`). The run record has no attempt field: attempts are numbered here by the start time of the Worker run.
- The Live State of the two missions was not read: the board ops store is Postgres behind a DSN, and the runner API GET route was not reachable on the local check.
- Scores 35, 38 and 58 of `ahawr-cloud-thinking-control` T017 are the stored values, and they match the three attempts named in the task.
- `docs/cloud-thinking-notes.md` (lines 380-399) lists T017 as 38 "as of attempt 3". The stored attempt 3 score is 58; 38 is attempt 2.

### ahawr-cloud-thinking-control, task T017 (Mission report, Russian)

Title: `TASK T017: Mission report (Russian)`

Objective (verbatim, from the Worker run `run_7b53e510225141f68988c468854f250b`):

> Read the results of the previous tasks: docs/cloud-thinking.md, claude-runner/docs/cloud-thinking-notes.md (including '## Validation'), the new and changed files, the data-tables/ops patch (if any) and the CHANGELOG entry. Return as the final answer a report of the whole mission in Russian covering: what the CLI can and cannot switch per call (with the version); the chosen mapping and the windows it does not reproduce; the files changed or added; the tests and the ruff/mypy results; the Run Manager forwarding verdict and the patch path; operator commands to enable it for the Haiku missions 'ahawr-dashboard-board-views' and 'ahawr-dashboard-board-views-2' (listed, not run); the version proposal; what remains open. Do not change any files.

Reviewer scores by attempt: attempt 1 = 35 (`run_f145c7edbc61492487f039dbdd50c70f`), attempt 2 = 38 (`run_1cc8f30a40044570a5ab410280115c64`), attempt 3 = 58 (`run_20b72091309c40d5a27b0dce8d621205`).

Cause 1 - no facts at hand (the report had no task list to work from)

- Mission `ahawr-cloud-thinking-control`, task T017, attempt 1, score 35:
  > "It says outright that it did not find the T001-T016 list and so cannot say each task is covered. It does not map the work to the tasks T001-T016, and the objective asks for that."
- Mission `ahawr-cloud-thinking-control`, task T017, attempt 2, score 38:
  > "The report admits it found no task list, then fills T006-T013 'by meaning'. It assigns T008-T011 to test files with no task-list source."
- Mission `ahawr-cloud-thinking-control`, task T017, attempt 3, score 58:
  > "The report fails acceptance criterion 1, since 11 of the 16 T001-T016 rows (T006-T013, T015, T016) are 'no evidence' and T001-T005 are only a group. That comes from a task list the report never found."

Cause 2 - weak local Russian synthesis (the report text is internally inconsistent)

- Mission `ahawr-cloud-thinking-control`, task T017, attempt 2, score 38:
  > "It also calls T013 and T016 'the current report' and 'below'. The report never says that T017 is the report task."
- Mission `ahawr-cloud-thinking-control`, task T017, attempt 3, score 58:
  > "The mypy count is still inconsistent: the notes say 64, the file sum is 68, and the report leaves both."

Cause 3 - no file may be changed (the Worker could not fix what the report found)

- Mission `ahawr-cloud-thinking-control`, task T017, attempt 2, score 38:
  > "The CHANGELOG mismatch is real. CHANGELOG.md:6 still reads 'Version: 0.7.500' while CHANGELOG.md:8 is '2026-10-20 (0.8.0)'. The report notes it but does not fix it, and the previous review asked for the mismatch to be fixed."
- Mission `ahawr-dashboard-board-views-2`, task T015, attempt 2, score 25 (`run_4138af0c`, 2026-10-19 05:48): the Worker saved the report to a file in `/tmp` instead of returning it.
  > "The Worker did not return the report. It only returned a short status message that says the report was saved to /tmp/t15_report_ru.md. The task requires the report itself as the final answer, and a file in /tmp is not a deliverable."

Cause 4 - Reviewer demands evidence the Worker cannot produce (checks not rerun in the session)

- Mission `ahawr-cloud-thinking-control`, task T017, attempt 1, score 35:
  > "(2) The key claims were not checked in this session: the CLI version 2.1.286, the test, ruff and mypy totals, the Run Manager forwarding verdict, and the patch path."
- Mission `ahawr-cloud-thinking-control`, task T017, attempt 2, score 38:
  > "(4) Several numbers were not rechecked in this session. These are the 717 passed / 20 mission tests figure, the ruff result (the report cites 9 errors in board_think.py, which contradicts the 'clean' claim), and the mypy figures."
- Mission `ahawr-cloud-thinking-control`, task T017, attempt 3, score 58:
  > "(5) The report asserts the 'git status identical' claim without showing it, and I did not recheck it. The test, ruff and mypy totals come from notes only and were not rerun this session."

### ahawr-dashboard-board-views-2, task T015 (Mission report, Russian)

Title: `TASK T015: Mission report (Russian)`

Objective (verbatim, from the Worker run `run_782c4d55`, 2026-10-19 04:08):

> Run the full checks: node --test claude-runner/tests/, python -m pytest claude-runner/tests -q, and the e2e test. Read the results of T001-T014 (the notes file, the new and changed files, docs/board-views.md, CHANGELOG.md). Return as the final answer a report in Russian: what was done per part A-G, the result paths, the checks run with their results, whether board-ui2 files were present and how they were integrated, what was not checked (for example the live dashboard before rebuild), the proposed version, and the operator commands (container rebuild, read-only GET checks). Do not change any file

Reviewer scores by attempt: attempt 1 = 30 (`run_686dae85`), attempt 2 = 25 (`run_4138af0c`), attempt 3 = 45 (`run_3d22219e`), attempt 4 = 35 (`run_399a115f`). The Worker run of attempt 5 (`run_4ab3dcbc`, 06:15) has no Reviewer record in the store.

## Workflow hook points

Source: `/tmp/ahawr_t004/wf_live_export_2026-10-26.json`, a copy outside the repository of `data-tables/ops/wf_before_ahawr-report-pack.json`. That file is a live export of workflow `7oMrsFqTiYMy0JJN` written by `launch_mission.sh` on 2026-10-26 (before patching). It is not a fresh export: the Docker CLI is not available in the shell of this run, so no new `export:workflow` was made. The workflow has 102 nodes. It already contains the `AHAWR_BLIND_GUARD`, think-policy and "(out of N)" patches, so the anchors below are the post-patch text.

Anchors are matched with `grep -F -c` on the export copy (each file is one JSON line, so `grep -o -F ... | wc -l` gives the occurrence count):

| Hook point | Node | Anchor (exact text) | Occurrences |
|---|---|---|---|
| Constants (new flag goes before the closing `} }];`) | `Constants` | `mission_roles_json: String(mission.roles_json ?? '').trim()` | 1 |
| Architect planning prompt | `Build Architect Run Input` | `$('Prompt Templates').first().json.architect_prompt ?? '',` | 1 |
| Worker run input | `Build Worker Run Input` | `  working_directory: ($('Mission').first().json.working_directory),` | 1 |
| Reviewer run input | `Build Reviewer Run Input` | `  session_id: (String($('Reviewer State').first().json.reviewer_session_id ?? ''))` | 1 |
| Review parsing (pass status) | `Parse Review` | `return ['pass','needs_changes'].includes(status)` | 1 |
| Accept condition (acceptance threshold) | `Review Passed?` | `"rightValue":"pass"` | 1 |
| Attempt counter (max) | `Retry Current Task` | `const maxAttempts = Number($('Constants').first().json.max_attempts_per_task ?? 3);` | 1 |
| Attempt counter (current) | `Retry Current Task` | `const currentAttempt = Math.max(Number(state.task_attempt ?? 1), loggedAttempt);` | 1 |
| Telegram task notice | `Split Task Notice` | `(total ? ' (out of ' + total + ')' : '')` | 1 |

Notes:

- Acceptance threshold: the workflow has no numeric pass score. `grep -o -F ">= 70"` returns 0 occurrences; the only `< 70` is the blind guard in `Retry Current Task`. A review is accepted when the parsed `review_status` equals `pass` (`Review Passed?`). The numeric bar, if any, lives in the Reviewer prompt, which is loaded from a data table and is not in the export.
- The Telegram path is `Split Task Notice`, which builds the TASK STARTED sections (`AHAWR_NOTICE_SPLIT`). The send nodes after it were not traced.
- Helpers to reuse from the existing patches (`data-tables/ops/`): `sub(code, old, new, what)` in `cloud_first_patch.py` and `think_config_patch.py` (asserts the anchor occurs exactly once); `node(w, name)` in `think_config_patch.py`; the `MARK` idempotency check and temp-file-then-`os.replace` write in `think_config_patch.py`; the `next(a for a in (...) if js.count(a) == 1)` anchor choice in `blind_guard_patch.py`.
- The `CONSTANTS_OLD` anchor in `think_config_patch.py` (`retry_delays: retryDelays`) no longer matches this export. A new Constants flag must take its anchor from the current export, not from the patch file.
- `review_calibration_patch.py` does not exist in `data-tables/ops/`.

## Runner integration

Read-only map for the new `POST /v1/report-facts` route (task T005). Nothing here is implemented yet.

Route pattern to copy:
- `claude-runner/src/claude_runner/run_views.py:96` `build_router(get_manager)` creates an `APIRouter`; `api.py:287-289` mounts it with `app.include_router(build_router(...), prefix="/v1", dependencies=[Depends(authorize)])`, so a route added there gets `/v1` and the auth check.
- Closest POST with a JSON body is `@router.post("/stats/runs")` at `run_views.py:124`, handler `run_stats(body: StatsBody)` at `run_views.py:125`, returning a plain dict. Its request model is `StatsBody` at `run_views.py:92`: `run_ids: list[str] = Field(default_factory=list, max_length=2000)`.
- Top-level POST pattern in `api.py`: `@app.post("/v1/qa/requests", dependencies=[Depends(authorize)])` at `api.py:394`, with a pydantic body `QaRequestBody` at `api.py:44` (`Field(min_length=1)` on required strings).
- Auth: `authorize` is defined in `api.py:202`. App version `api.py:200` `version="0.10.100"`; `pyproject.toml:7` `version = "0.10.100"`.

Proposed shape (the field list is not fixed by this task):
- Request: `POST /v1/report-facts`, body model `ReportFactsBody(BaseModel)` in the style of `StatsBody`, with `run_ids: list[str]` bounded by `max_length`, plus any mission fields the design adds.
- Response: a JSON object whose `markdown` key holds the report facts as a markdown string.

Run summary fields to reuse (no code is duplicated):
- `run_views.py:30` `_change_stats(entries, cwd)` computes `compactions`, `steps`, `edits` and `files_changed` (`run_views.py:46-49`), capped by `MAX_FILES_CHANGED`.
- `run_views.py:53` `summary(run, manager)` builds the per-run dict; `run_views.py:110-122` returns it with events.
- `mission_stats.py:29` `_run_ids_with_compactions(manager, run_ids)` and `stats_for_runs` (used by `run_stats`) give time, cost and token totals for a list of runs.

Helpers to reuse:
- Git with timeout: `claude-runner/src/claude_runner/qa_worker.py:118` `_git_lines(args, working_directory)` runs `subprocess.run(..., timeout=GIT_TIMEOUT_SECONDS, check=False)`; `GIT_TIMEOUT_SECONDS = 30` at `qa_worker.py:61`; `changed_files` at `qa_worker.py:135`. It is private (`_`-prefixed); importing it means either renaming it or moving it to a shared module. Decide that in the task, not here.
- Async subprocess with a timeout: `api.py:98` `_cli_version`, using `asyncio.wait_for(..., 30)`.
- JSON file read: no general helper exists. `qa_fixture.py:90` `_read_json(path)` reads a file and raises `ValueError` with the path. `board_pg.py:285` and `board_ops_store_pg.py:44` parse jsonb DB columns, not files, so they are not suitable.
- Redaction: a helper already exists. `claude-runner/src/claude_runner/provider_secrets.py:41` `redact(text, secrets)` replaces each non-empty secret value with `REDACTED_TEXT` (`provider_secrets.py:23`, `"[REDACTED]"`), longest first. The caller supplies the secret values. `provider_secrets.py` also has `SecretRedactionFilter` for logs. `board_pg.py:1532` `_redact_dsn` is DSN-specific. `guards.py` only names the Telegram variables (`guards.py:31-34`) and never returns their values. So `report_facts.py` does not need its own redaction helper: it should call `provider_secrets.redact`.

mission_lint:
- `data-tables/ops/mission_lint.py` exists. It is not in git (operator scripts). Its rules are checked against the mission objective; `docs/mission-authoring.md:19` describes the warnings. The file is not read for this section.

## Design

The five decided points (a)-(e), from the mission OBJECTIVE section "DESIGN (decided, do not reopen)":

- (a) The FACTS PACK is generated by code, not by the model, and is given to the Worker and to the Reviewer as the same authoritative block.
- (b) The report task runs on role `report` of `roles_json` (from mission ahawr-worker-attempt-chain, see `claude-runner/src/claude_runner/roles_config.py` and `claude-runner/docs/roles-config.md`). Its default is the `roles_json` default, `{"1": "anthropic/haiku", "3": "anthropic/sonnet"}`: the first model on attempts 1-2 and the second from attempt 3, with a fresh session when the model changes. It replaces repeating the local model up to five times. The `report` role falls back to `worker` when unset. No model name is hard-coded in the patch, and no new columns or fields are added.
- (c) The report task may write exactly one file, `reports/<mission_id>-report-ru.md` in the working directory, and nothing else.
- (d) The Reviewer gets a report-specific rubric: an honestly stated unmet criterion or open defect is not a defect of the report. The score depends on accuracy against the facts pack, citing sources, completeness of the required sections and the Russian text. The acceptance threshold of a report task is 70.
- (e) Everything is behind the flag `report_pack` in the workflow Constants, default 0. With it off, the plan, the prompts and the Worker input are byte-for-byte what they are today.

## Facts pack

The facts pack is generated by the runner for the report task. Its sections appear in this order:

1. Mission id and mission name.
2. Numbered acceptance criteria of the mission, copied from the stored objective.
3. Task table: id, title, status, attempts, score and files, one row per task. The Reviewer score is the stored value of the last attempt.
4. Files changed: `git diff --stat` of the uncommitted changes and of the commits made in the mission window, followed by the names of new untracked files.
5. Quoted check lines: only lines of the pytest, ruff and mypy output that match `passed`, `failed`, `error`, `Found N errors` or `Success`.
6. Unresolved Reviewer remarks of the last attempt of each task, first 300 characters of each.
7. Known unmet: the acceptance criteria of the tasks that were not accepted.

The pack starts with the header line `FACTS (generated, authoritative, do not contradict, do not invent rows)`.

No evidence rule: a field with no stored or measured value is written as `no evidence`. The generator never fills a field from the model's own text.

Redaction rule: every value of a secret, token or environment variable is removed before the pack is built, with `provider_secrets.redact` (`claude-runner/src/claude_runner/provider_secrets.py:41`). Raw logs and prompts are not included.

Limit: the whole pack is at most 12000 characters.

Cut rule, in this fixed order, until the pack fits:

1. Drop the oldest Reviewer remarks first, one attempt at a time, from the oldest attempt.
2. Shorten the file lists in section 4, keeping the file names and cutting the diff counts first.
3. Never cut the task table (section 3) or the numbered criteria (section 2).

The pack always ends with the marker line `END OF FACTS`. When any cut was made, a line `CUT: <n> characters removed` is placed just before it.

## Escalation

- The model of the report task is read from role `report` in `roles_json`. The default map, used when the role is absent, is `{"1": "anthropic/haiku", "3": "anthropic/sonnet"}`, keyed by attempt number. This default is the `roles_json` default, not a model name fixed in the patch.
- If `roles_json` has no role `report`, the role `worker` is used instead.
- A change of model between attempts starts a fresh session.
- After two failed attempts of the report task, the Telegram notice `report task failing, handed to sonnet` is sent.

## Detection

- A task is a report task when its `kind` is `report`.
- Fallback when `kind` is missing: the title contains `report`, `отчёт` or `отчет`. The fallback applies only to the last task of the mission, never to other tasks.

## Not changed

This task changes only this document. It does not change the workflow export or any `data-tables/ops` patch, the `roles_json` or `features_json` data, the runner code, the board, the database schema, the API, or the version number.

## Rollback

The feature is behind the flag `report_pack`, which is OFF by default. With `report_pack = 0` the behaviour is byte-for-byte the current behaviour. To roll back, set the flag to 0; no data needs to be removed.

## Report template

The objective text below is copied verbatim into the report task by the patch. `<mission_id>` is replaced by the mission id at run time.

```text
Write the mission report in Russian for mission <mission_id>. The block FACTS (generated, authoritative, do not contradict, do not invent rows) is the only source of task rows, numbers and check lines: copy them, do not change them and do not add to them.

The report has these seven sections, in this order, with these Russian headings:

1. Результат
2. Критерии приёмки
3. Что изменено
4. Тесты и проверки
5. Открытые вопросы и дефекты
6. Команды оператора (не запускались)
7. Предлагаемая версия

Rules for each section:
- Результат: the outcome of the mission in a few sentences, with a source for each claim.
- Критерии приёмки: answer every numbered acceptance criterion of the FACTS block one by one, with the status выполнен (met), не выполнен (not met) or частично (partly) and a source (file:line, a stored Reviewer remark or a check line). Do not skip a criterion.
- Что изменено: list the files from section 4 of the FACTS block and no others.
- Тесты и проверки: quote the check lines from section 5 of the FACTS block. For any check that did not run, write "не запускалась" and the reason.
- Открытые вопросы и дефекты: list the known unmet items from section 7 of the FACTS block and the open Reviewer remarks. State them honestly.
- Команды оператора (не запускались): list the operator commands, each marked "не запускалась". Do not run them.
- Предлагаемая версия: the proposed version number with its reason. Do not change any version file.

General rules:
- Cite a source for every number and every claim. Do not invent a task, a file, a number or a check result.
- Write the report to reports/<mission_id>-report-ru.md in the working directory. That is the only file you may write. Do not change any other file.
- Return the full report text as the final answer. A status message or a file path alone is not a report.
- Do not commit, push, or run git commands that change the repository.
```

## Reviewer rubric

The rubric below is the Reviewer prompt text for a report task. The threshold is 70 points out of 100.

```text
Reviewer rubric for the mission report task. Score the report out of 100. The pass threshold is 70.

Check the report against the FACTS block of the task and the sources it cites.

1. Accuracy (40 points): every table row, number and check line in the report matches the FACTS block. A row, number or check line that is not in the FACTS block, or that differs from it, loses points. A contradiction between two sections loses more.
2. Completeness (30 points): all seven sections are present, in the fixed order, with their Russian headings. Every acceptance criterion of the mission is answered one by one with a status. Every file, check and operator command from the FACTS block is covered.
3. Sources (20 points): every claim, number and status cites a source: file:line, a stored Reviewer remark, or a check line. A claim without a source loses points.
4. Language (10 points): the report text is Russian. Code, file names, identifiers and quoted text may stay as they are.

What is NOT a defect:
- An acceptance criterion honestly stated as не выполнен (not met) or частично (partly), with its reason and source.
- An open defect or an open point stated honestly in section 5.
- A failure that predates the mission, when the report says so and cites its source.
- A check marked "не запускалась" with the reason.

Exactly one file must be written: reports/<mission_id>-report-ru.md. Any other file written, changed or committed is a defect.

Pass: total score of at least 70. Below 70 the attempt fails.
```
