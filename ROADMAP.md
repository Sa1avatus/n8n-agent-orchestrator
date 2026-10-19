# AHAWR Development Roadmap

_Agentic Hub for Automation Workflow Routing · 2026 – 2027_

**Page version:** https://sa1avatus.github.io/n8n-agent-orchestrator/roadmap.html

AHAWR runs long coding missions on local and cloud models: an Architect plans, a Worker implements, a Reviewer scores, and n8n drives the loop. This roadmap lists the milestones from the first workflows to version 1.0.

> **Note:** The plan follows the mission queue (data-tables/ops/real_queue.sh) and the changelog. Dates are start dates of the work; the order of the next missions can change when a mission fails or a decision is made. Hardware tuning of the llama.cpp fork is tracked in its own repository.

## Mission Board Is Live

**0.6.100 · October 19, 2026** — Board views, task cards, operations control and translations on the dashboard — next 0.7.0

Current version: **0.6.100** · Missions completed: **9** · Missions in the queue: **13** · Next milestone: **0.7.0 · 1.0.0**

The board shows every mission with its tasks, attempts, time, tokens and cost in five views (Projects, Kanban, Lanes, Sprint, Grid), orders tasks by their dependencies, edits the plan and the project rules, and speaks English and Russian. The guards that watch the runner move into the dashboard next.

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

0.5.0 · 0.5.100 · The queue and the missions as cards · 5/5 milestones, 100%

- [x] Five groups of cards with statistics
- [x] Queue drag-and-drop with version checks
- [x] Mission form with guarded saves
- [x] Board statistics over HTTP, no wait for the retrieval sync
- [x] Run page scroll and polling fixes

### Board 0.6.0: views, tasks, operations — Done (October 18 – 19)

0.6.0 · Everything the first board mission left open · 6/6 milestones, 100%

- [x] Operations control: process control, loop protection (ops-control)
- [x] Task cards, plan editing, thinking panel, readiness and history (board-ui2)
- [x] Board views part 1: summary route, project rules, i18n tables (Haiku 5.5)
- [x] Board views part 2: smart task ordering, Lanes and Sprint views (Haiku 5.5, report of the last task written by hand)
- [x] Roadmap page (GitHub Pages), ROADMAP.md and the new README (0.5.200 – 0.5.300)
- [x] Release 0.6.0: tests and browser checks

### Reliability and control — Next (Q4 2026)

0.7.0 and up · Guards, adaptive split and the executor settings · 0/8 milestones, 0%

- [ ] 0.7.0: rename claude-runner to ahawr-runner (folder, package, container, CLAUDE_RUNNER_* aliases), after the 0.6.0 release at a queue pause
- [ ] ahawr-guards-in-dashboard: all watchers inside the dashboard container
- [ ] ahawr-cloud-thinking-control: thinking policy for cloud Workers
- [ ] ahawr-worker-attempt-chain: provider and model per attempt as a setting
- [ ] ahawr-improvements-v1 / v2: Reviewer calibration, MCP search, nightly Worker suite
- [ ] ahawr-adaptive-split: plan size gate and automatic split
- [ ] ahawr-rag-quality: offline evaluation and lexical precision
- [ ] Dashboard over the local network and on mobile

### Version 1.0: more executors — Next (Q4 2026 – Q1 2027)

1.0.0 · Codex as a third executor next to Hermes and Claude Code · 0/4 milestones, 0%

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

---
Generated by `scripts/make_roadmap.py` from `docs/roadmap.json` · AHAWR
