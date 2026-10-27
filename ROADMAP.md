# AHAWR Development Roadmap

_Agentic Hub for Automation Workflow Routing · 2026 – 2027_

**Page version:** https://sa1avatus.github.io/n8n-agent-orchestrator/roadmap.html

AHAWR runs long coding missions on local and cloud models: an Architect plans, a Worker implements, a Reviewer scores, and n8n drives the loop. This roadmap lists the milestones from the first workflows to version 1.0.

> **Note:** The plan follows the mission queue (data-tables/ops/real_queue.sh) and the changelog. Dates are start dates of the work; the order of the next missions can change when a mission fails or a decision is made. Hardware tuning of the llama.cpp fork is tracked in its own repository.

## Mission Board Is Live

**0.11.300 · October 27, 2026** — Host actions, the queue from the board, providers in the web UI and the report pack are in 0.11.0; the JSON configuration comes next

Current version: **0.11.300** · Missions completed: **26** · Missions in the queue: **25** · Next milestone: **0.12.0 · 1.0.0**

The board shows every mission with its tasks, attempts, time, tokens and cost in five views (Projects, Kanban, Lanes, Sprint, Grid), a Guards tab with the guards of the dashboard, orders tasks by their dependencies, edits the plan and the project rules, and speaks English and Russian. QA runs on the host through a request queue; the mission queue moves to the board next.

## Timeline

### Foundations — Done (September 2026)

First workflows · n8n + Hermes, Data Tables, prompts and missions · 4/4 milestones, 100%

- [x] First n8n and Hermes workflows (AHAWR, Run Manager)
- [x] Data Tables for config, prompts and missions
- [x] Dockerfile and scripts
- [x] Bug fixes

### AHAWR on Claude Code — Done (September 27 – October 11)

claude-runner + LiteLLM + local llama · One runner API for Claude Code and the local model · 8/8 milestones, 100%

- [x] claude-runner runs the Claude Code CLI headless behind /v1/runs
- [x] LiteLLM between Claude Code and the local llama.cpp
- [x] Retrieval service for the Worker and the Reviewer
- [x] Thinking policy, compaction and retry tuning
- [x] Telegram notices and a control bot
- [x] Dashboard with live runs, cost and cache
- [x] Prefill and speed missions on llama.cpp
- [x] Model benchmark: cloud and local Workers

### Postgres and thinking switch — Done (October 12)

0.3.0 · A stable store and per-request thinking control · 5/5 milestones, 100%

- [x] n8n database moved from SQLite to Postgres 16
- [x] Thinking switched without a llama restart
- [x] Telegram task notice split into several messages
- [x] Worker scratch folder survives container recreation
- [x] Reviewer can search any folder

### Dashboard filters and search — Done (October 14 – 15)

0.4.0 · 0.4.100 · 0.4.200 · Clickable filter values and full-text search over runs · 4/4 milestones, 100%

- [x] Every value of a run is a click-filter, combined with AND/OR
- [x] Filter persisted in the URL hash
- [x] Full-text search over event logs
- [x] Run-list fixes and the version rule

### Mission board — Done (October 17)

0.5.0 · 0.5.100 · The queue and the missions as cards · 6/6 milestones, 100%

- [x] Five groups of cards with statistics
- [x] Queue drag-and-drop with version checks
- [x] Mission form with guarded saves
- [x] Board statistics over HTTP, no wait for the retrieval sync
- [x] Run page scroll and polling fixes
- [x] Roadmap page (GitHub Pages), ROADMAP.md and the new README (0.5.200 – 0.5.300)

### Board 0.6: views, task cards, operations control — Done (October 18 – 19)

0.6.0 · 0.6.100 · Four board missions in one release: operations control, task cards, views and translations · 5/5 milestones, 100%

- [x] Operations control: process control, loop protection (ops-control)
- [x] Task cards, plan editing, thinking panel, readiness and history (board-ui2)
- [x] Board views part 1: summary route, project rules, i18n tables (Haiku 5.5)
- [x] Board views part 2: smart task ordering, Lanes and Sprint views (Haiku 5.5, report of the last task written by hand)
- [x] Releases 0.6.0 and 0.6.100: tests and browser checks

### Board 0.7: settings window and board fixes — Done (October 19)

0.7.0 · 0.7.100 – 0.7.400 · Mission settings in a step-by-step window; fixes of statuses, languages and Kanban drag-and-drop · 4/4 milestones, 100%

- [x] Launch settings window with steps, Review table and expand (0.7.0); data tables no longer bloat
- [x] Fixes 0.7.100 – 0.7.400: launch settings fields, task statuses, board languages, Kanban drag-and-drop and the queue
- [x] Documentation cleaned: mission run notes and reports removed from docs/
- [x] Releases 0.7.0 – 0.7.400: tests and browser checks

### 0.8.0: guards in the dashboard, cloud thinking, adaptive split — Done (October 21)

0.8.0 · Guard supervisor, thinking control for cloud runs, plan size gate with automatic split, the agent guide · 4/4 milestones, 100%

- [x] ahawr-guards-in-dashboard: all watchers inside the dashboard container (0.8.0; the migration of the host watchers is ahawr-guards-migration)
- [x] ahawr-cloud-thinking-control: thinking policy for cloud Workers (0.8.0)
- [x] ahawr-adaptive-split: plan size gate and automatic split (0.8.0; files and patch, not yet applied to the live workflow)
- [x] CLAUDE.md: the agent guide for the Worker, Reviewer and Architect

### 0.9.0: QA, guards migration and the Guards tab — Done (October 21 – 22)

0.9.0 · QA role and runner, guards moved into the dashboard container with their own tab, attempt chains · 5/5 milestones, 100%

- [x] ahawr-worker-attempt-chain: provider and model per attempt as a setting (0.9.0)
- [x] ahawr-qa-acceptance: QA by user-level UI scenarios (Playwright on a throwaway stack) at the end of a mission and nightly, not after every task (0.9.0; QA is OFF by default and is connected to the workflow after ahawr-features-config)
- [x] ahawr-guards-migration: guards of the host watchers moved into the dashboard container, host launcher and heartbeats (0.9.0; the notice guards wait for a host publisher of the queue log)
- [x] Guards tab in the dashboard: cards, flow and timelines with a history of checks (0.9.0)
- [x] Logo: the Result gear is replaced by a QA gear

### 0.10.0: features config and all of QA — Done (October 22 – 23)

0.10.0 · Workflow features as one JSON setting with a live adaptive split, and all of QA in one version: a request queue, its own container, regression, API and UI layers, and a smoke crawl · 8/8 milestones, 100%

- [x] ahawr-features-config: features_json for the workflow features, surgical split patch, Features section in the launch settings, Telegram progress numbers; the live proof of the split is an operator step
- [x] ahawr-qa-gate: QA runs on the host through a request queue in the runner, the workflow asks and waits; a fail sends the mission back at most twice, a blocked QA never rejects (switched on 2026-10-22, the first live mission with UI changes is still to come)
- [x] Kanban: an interrupted mission that stays in the queue waits in Queued instead of Running; the board queue is synchronised with the queue shell
- [x] Telegram relay runs in the dashboard container; the host relay is retired
- [x] ahawr-qa-container: QA in its own Linux container (Playwright, pytest, ruff, mypy, node), read-only mounts, no Docker socket, a light in-process stack for the UI and API layers (built and run once: the API layer passes in the container, the in-process UI layer fails 9 of 17 scenarios, so UI QA stays on the host; the image, the parity check, retiring the host worker and closing the runner port are operator steps)
- [x] ahawr-qa-layers: regression (pytest, node, ruff, mypy against a baseline), API routes, UI scenarios with smoke, nightly run; the QA panel mounted on the mission page
- [x] ahawr-qa-smoke: universal QA smoke crawl without per-mission scenarios (the crawl does not yet reproduce the three replayed bugs: a drag and a forced 409 are the next step)
- [x] 0.10.0 release: changelog, version, roadmap. Open QA steps: make the in-process UI stack pass all 17 scenarios, take the regression baseline, retire the host worker, close the runner port (not before that)

### 0.11.0: host actions, the queue from the board, providers and report tasks — Done (October 24 – 27)

0.11.0 · Container actions by request with operator confirmation, board.queue as the single source of the mission order, providers in the web UI and reports that pass review · 5/5 milestones, 100%

- [x] ahawr-host-actions: a request queue for recreate and restart of a container with a whitelist and safe conditions: automatic for the dashboard and helper services, operator confirmation for claude-runner, n8n and llama (flag HOST_ACTIONS, off by default)
- [x] ahawr-queue-from-board: the queue shell reads the next mission from board.queue (on by default, QUEUE_FROM_BOARD=0 turns it off), fallback to the static list
- [x] ahawr-providers-ui: provider type (API compatibility: OpenAI, Anthropic, others), models, prices and write-only API keys in the web UI
- [x] ahawr-report-pack: a generated facts pack (report_facts.py), a Reviewer rubric and a Telegram notice after two failed report attempts, as a workflow patch behind the flag report_pack (off by default; the patch is imported into the live workflow, the flag is not turned on and the cloud Worker for reports is not set yet)
- [x] 0.11.0 release: changelog, version, roadmap (committed, not pushed)

### 0.12.0: configuration columns to JSON and the rename to ahawr-runner — Next (Q1 2027)

0.12.0 · hermes_config from 41 to 10 columns, one JSON setting per concern; claude-runner becomes ahawr-runner at a queue pause · 0/3 milestones, 0%

- [ ] Configuration columns to JSON (docs/config-columns-plan.md): roles_json, thinking_json, retrieval_json, engine_json, missions overrides_json; the legacy columns are removed one release after the JSON works
- [ ] Rename claude-runner to ahawr-runner (folder, package, container, CLAUDE_RUNNER_* aliases), at a queue pause, after the JSON configuration works
- [ ] 0.12.0 release: changelog, version, roadmap

### Quality and reach (no version fixed) — Next (Q4 2026 – Q1 2027)

after 0.11.0 · Reviewer calibration, retrieval quality and the dashboard on the local network · 0/3 milestones, 0%

- [ ] ahawr-improvements-v1 / v2: Reviewer calibration, MCP search, nightly Worker suite
- [ ] ahawr-rag-quality: offline evaluation and lexical precision
- [ ] Dashboard over the local network and on mobile

### Version 1.0: more executors — Next (Q4 2026 – Q1 2027)

1.0.0 · Codex as a third executor next to Hermes and Claude Code · 0/5 milestones, 0%

- [ ] Code review and refactoring mission (before 1.0, after the 0.x feature and JSON configuration missions, before the Codex executor: 1.0 freezes the formats): dead code, duplicate functions, hard-coded values that should be settings, names brought to docs/naming.md, import and run-time cycles, a measured performance pass, old stop-gaps, mypy and ruff debt, scripts outside git (plan: docs/review-refactor-plan.md)
- [ ] ahawr-codex-executor: the OpenAI Codex CLI as an engine of a provider block
- [ ] DeepSeek Harness evaluated as a third executor
- [ ] Stable provider-block configuration and event format
- [ ] 1.0.0 release: changelog, README “What 1.0 means”, roadmap

### Parallel missions and Hermes parity — Later (2027)

after 1.0 · Several missions at once and Hermes parity · 0/3 milestones, 0%

- [ ] ahawr-parallel-missions: one workflow, per-mission model and folder
- [ ] Hermes runs on par with Claude Code on the dashboard
- [ ] Mission templates and A/B of executors

## Open decisions

- Haiku 5.5 vs local Qwen: first attempt on Haiku, retries on the local model is running on the board missions; a full A/B is on hold.
- RAG precision stays at 8.6%: the quality mission is queued.
- Executors for 1.0: Hermes and Claude Code already work; Codex comes first as the third, DeepSeek Harness is evaluated before it becomes an engine of a provider block.
- Problem: The agent shell of a Worker has no docker and no Playwright: tasks that need them are done by the operator on the host (a fresh workflow export, QA runs).
- Problem: The local Worker loops on hard tasks (T010B ran 160+ steps): adaptive split is not live yet, so wide tasks are not cut automatically.
- Problem: A Worker ran git stash against the rule and hid uncommitted work; restored from the stash. Missions now forbid stash; a technical guard is open.
- Problem: The notice guards (mission_notice, idle) cannot leave the host until a host publisher fills board.queue_log; queue_watch.py stays on the host until then.
- Problem: The tg_relay guard reports ok while the relay logs HTTP 409; relay errors are not yet part of the guard status.
- Problem: The QA nodes of the workflow are proven only on a copy of the export: the first live mission with UI changes is the real test; the board QA panel is not mounted yet (ahawr-qa-layers).
- Problem: The mission queue exists twice (real_queue.sh and board.queue): synchronised by hand until ahawr-queue-from-board.
- Problem: gpu_power_governor is not running since 2026-10-19 (the operator decides); llamacpp-prefill-kernels failed earlier and waits for a decision.
- Problem: Telegram token and database password rotation are pending; the runner API key is empty (no bearer check) and the runner port is published on the loopback only.
- Problem: Reviewers sometimes accept a task that was not run, or reject for a missing file the agent shell cannot create: calibration is in ahawr-improvements.
- Problem: QA is executed by a host process with the operator's rights, and the Windows host fails about 100 tests for POSIX reasons (the regression baseline would be dirty): ahawr-qa-container moves QA into a Linux container without the Docker socket.

---
Generated by `scripts/make_roadmap.py` from `docs/roadmap.json` · AHAWR
