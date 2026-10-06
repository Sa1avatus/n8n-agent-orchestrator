# n8n Agent Orchestrator (AHAWR)

**English** | [Русская версия](#русская-версия)

An n8n-based orchestration system for running autonomous multi-agent development workflows. The roles run either through a Hermes Gateway or through Claude Code (`claude-runner`, with Anthropic models or a local llama.cpp model); see [AHAWR on Claude Code](#ahawr-on-claude-code-claude-runner). Recent changes: [`CHANGELOG.md`](CHANGELOG.md). Current version: **0.2.0** (see [Versioning](#versioning)). Why the stack is built the way it is (English-first plans, short compactions, search hooks, operator acceptance): [Why it works this way](#why-it-works-this-way-constraints-and-workarounds).

The system separates **workflow logic**, **runtime configuration**, **agent prompts**, **missions**, and **secrets** so that the workflow can be maintained and reused without editing the main orchestration graph every time a model or mission changes.

---

## English

### What this project does

This project turns n8n into an orchestration layer for three logical AI roles:

```text
                         ┌─────────────────────┐
                         │       n8n           │
                         │   AHAWR workflow    │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ Architect / Planner │
                         │ creates a task plan │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │       Worker        │
                         │ executes one task   │
                         │ through Hermes      │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │      Reviewer       │
                         │ checks the result   │
                         └──────────┬──────────┘
                                    │
                            pass / retry / next
```

The main workflow is the **Agentic Hub for Automation Workflow Routing (AHAWR)** workflow.

It is designed for long-running tasks where a single LLM call is not enough. Instead of asking one model to solve everything, the workflow:

1. loads a mission and runtime configuration;
2. asks the Architect/Planner to decompose the mission into tasks;
3. takes one task at a time;
4. starts a Worker run through Hermes;
5. polls Hermes until the run finishes or reaches the configured polling limit;
6. sends the Worker result to an independent Reviewer;
7. accepts the task or retries it;
8. moves to the next task after approval;
9. persists execution state so the workflow can recover from interruptions;
10. returns an overall success or failure result.

The workflow therefore behaves more like a small autonomous engineering pipeline than a simple chatbot.

### Components

#### `AHAWR_v13.json`

The main orchestration workflow.

Its responsibilities include:

- loading configuration from n8n Data Tables;
- loading prompts and mission data;
- starting the Architect/Planner;
- parsing the generated task plan;
- selecting the current task;
- starting and monitoring the Worker;
- starting and monitoring the Reviewer;
- handling retries;
- saving and restoring execution state;
- advancing to the next task;
- producing final `approved` or failure output.

The workflow is intentionally stateful. Task execution state is persisted using n8n Data Tables so that long-running asynchronous Hermes jobs do not have to exist only inside one transient execution.

#### `Hermes_Run_Manager_v5.json`

A reusable n8n sub-workflow that wraps Hermes Gateway operations.

It receives normalized runtime parameters such as:

- role;
- run ID;
- session ID;
- input;
- resume input;
- model;
- provider;
- polling interval;
- maximum polling attempts;
- provider retry limit;
- retry delay configuration.

Its purpose is to hide the low-level Hermes HTTP/poll/retry logic from the main AHAWR workflow.

The main workflow can therefore treat Hermes as a reusable execution service.

### Execution model

The normal control flow is:

```text
Mission
   │
   ▼
Load configuration
   │
   ▼
Architect
   │
   ▼
Parse plan
   │
   ├── Task 1
   │      │
   │      ▼
   │    Worker
   │      │
   │      ▼
   │    Reviewer
   │      │
   │      ├── PASS ──────────────► Next task
   │      │
   │      └── NEEDS_CHANGES ─────► Retry task
   │
   ├── Task 2
   │
   └── ...
   │
   ▼
Approved
```

### Asynchronous Hermes execution

Hermes runs are asynchronous.

A Worker or Reviewer is not treated as a single synchronous LLM request. The workflow:

```text
POST /v1/runs
      │
      ▼
receive run_id
      │
      ▼
wait
      │
      ▼
GET /v1/runs/{run_id}
      │
      ├── running → wait and poll again
      │
      ├── completed → process result
      │
      └── error → retry/fail
```

Polling limits and delays are configurable.

This is important for large models and long-running coding tasks where inference may take substantially longer than a normal HTTP request.

### Retry strategy

The system has two separate levels of retry logic.

**Provider retries** handle temporary failures while communicating with the model provider/Hermes.

**Task retries** handle a Reviewer result that says the task needs changes.

A task can therefore be retried without recreating the entire mission plan.

The configured retry delay sequence is stored as JSON, for example:

```text
[15, 30, 60, 120, 300]
```

The Hermes Run Manager accepts the retry-delay configuration and applies it when a retry is required.

### Persistence and recovery

AHAWR stores execution state in n8n Data Tables.

The state is associated with the mission-specific `state_namespace`.

This allows different missions to keep independent state and prevents one mission from accidentally reusing another mission's execution state.

The persisted information can include items such as:

- current task index;
- current task ID;
- task attempts;
- Architect run/session information;
- Worker run/session information;
- Reviewer run/session information;
- last review result;
- task and mission progress.

The important design principle is:

```text
mission → state_namespace → persisted state
```

### Configuration architecture

The project separates data into three logical Data Tables.

#### `hermes_config`

Runtime settings for model/provider selection and execution limits.

Typical fields include:

```text
profile_id
enabled
architect_model
architect_provider
worker_model
worker_provider
reviewer_model
reviewer_provider
max_tasks
max_attempts_per_task
architect_max_polls
worker_max_polls
reviewer_max_polls
architect_poll_seconds
worker_poll_seconds
reviewer_poll_seconds
architect_max_retries
worker_max_retries
reviewer_max_retries
retry_delays_json
```

This means that changing the model or retry policy does not require modifying the orchestration graph.

#### `agent_prompts`

Versioned prompts for the logical agents.

The important roles are:

- Architect / Planner;
- Worker;
- Reviewer.

Prompts are kept outside the main workflow so they can evolve independently from orchestration logic.

**Working language.** The plan, the tasks, the Worker's notes and reports and the reviews are in
English whatever the mission's language: the same text in English takes 5-15% fewer tokens for
Qwen and Claude, and the Worker sees one language instead of English rules plus a translated task.
Texts that must appear verbatim (paths, identifiers, strings and headings the mission requires)
stay in the original language. For a non-English mission the Architect adds a last task
`Mission report (<language>)`: the Worker returns a report of the whole mission in the mission's
language, the Reviewer checks it, and the `✅ WORKFLOW APPROVED` Telegram notice carries it.

#### `missions`

Mission definitions.

A mission contains the business/engineering objective plus rules and acceptance criteria.

A mission also defines its `state_namespace`, which is used for persistent execution state.

Example:

```text
mission_id: simple-readonly-review
state_namespace: test
```

This makes it possible to have multiple independent missions without changing the core workflow.

### Secrets

Secrets should **not** be stored in:

- `*.example.csv` and anything else under version control;
- workflow source files.

Use n8n Credentials, environment variables, or another secret-management mechanism.

The repository should contain only configuration that is safe to version-control.

Do not commit:

```text
.env
database files
runtime state
API keys
Bearer tokens
passwords
model weights
local logs
```

### Repository files

The repository contains:

```text
.
├── AHAWR_v13.json                   # AHAWR on Hermes (with the retrieval layer)
├── AHAWR_v13_ClaudeCode.json        # AHAWR on Claude Code (claude-runner)
├── Hermes_Run_Manager_v5.json       # run/poll/retry/compression sub-workflow for Hermes
├── Claude_Code_Run_Manager_v1.json  # the same for claude-runner
├── hermes_config.example.csv        # Data Table example: profiles for Hermes, Claude Code, local, OpenAI
├── agent_prompts.example.csv        # Data Table example: Architect / Worker / Reviewer prompts
├── missions.example.csv             # Data Table example: one mission
├── data-tables/                     # your live CSVs (gitignored): exports, mission files
├── claude-runner/                   # Claude Code CLI behind the /v1/runs API, dashboard
├── retrieval-service/               # ahawr-retrieval (Context Retrieval Layer)
├── litellm/                         # LiteLLM config and hook for the local llama.cpp model
├── docs/                            # retrieval docs, compaction analysis
├── scripts/                         # helper scripts
├── docker-compose.yml, Dockerfile   # the stack: n8n, claude-runner, ahawr-retrieval, litellm, dashboard
├── .env.example                     # every setting, with comments (copy to .env)
├── ARCHITECTURE_CONTRACT.md
├── CHANGELOG.md
└── README.md
```

What changed and when: [`CHANGELOG.md`](CHANGELOG.md).

### Requirements

The architecture assumes:

- n8n;
- Hermes Gateway;
- an LLM provider accessible through Hermes;
- optionally a local llama.cpp backend;
- n8n Data Tables;
- Docker/host networking configured so n8n can reach Hermes.

A local deployment can use an arrangement such as:

```text
n8n
  │
  ▼
Hermes Gateway
  │
  ▼
llama.cpp / other model provider
```

The exact hostnames, ports, credentials and model names are deployment-specific and should be configured outside the workflow source when possible.

For the current TUI compression transport, the n8n container reaches the Hermes WebSocket backend on port `9119`, while the normal Hermes HTTP run API remains deployment-specific.

### Installation

#### 1. Install Git

Install Git for your operating system:

https://git-scm.com/downloads

Verify:

```powershell
git --version
```

#### 2. Install Docker Desktop

Install Docker Desktop:

https://www.docker.com/products/docker-desktop/

On Windows, start Docker Desktop and make sure the Docker Engine is running.

Verify Docker and Docker Compose:

```powershell
docker --version
docker compose version
```

#### 3. Clone the repository

```powershell
git clone https://github.com/Sa1avatus/n8n-agent-orchestrator.git
cd n8n-agent-orchestrator
```

#### 4. Start n8n

After Docker Desktop is running:

```powershell
docker compose up -d
```

Check the containers:

```powershell
docker compose ps
```

View logs if necessary:

```powershell
docker compose logs -f
```

Open n8n using the port configured in `docker-compose.yml`.

#### 5. Prepare Hermes Gateway

Make sure Hermes Gateway is running and reachable from the n8n container.

For Docker Desktop, a host service can typically be reached through:

```text
http://host.docker.internal:8642
```

Use the actual Hermes address and port for your deployment.

#### 6. Prepare the LLM backend

Hermes must have access to the model provider used by AHAWR.

Typical topology:

```text
n8n
 │
 ▼
Hermes Gateway
 │
 ├── local llama.cpp
 │
 └── external model provider
```

Models and providers are configured through the `hermes_config` Data Table.

#### 7. Import the Run Manager

In n8n:

1. Import `Hermes_Run_Manager_v5.json` (Hermes variant) or `Claude_Code_Run_Manager_v1.json` (Claude Code variant, see [AHAWR on Claude Code](#ahawr-on-claude-code-claude-runner)).
2. Configure the required Hermes credentials.
3. Run a simple test request.
4. Confirm that Hermes can start a run and return a result.

Do not put real credentials into Git-tracked workflow files.

#### 8. Create the Data Tables

Create:

```text
hermes_config
agent_prompts
missions
```

Import the example CSV files from the repository root: `hermes_config.example.csv`,
`agent_prompts.example.csv`, `missions.example.csv` (`hermes_config` and `missions` use `;` as
the separator).

`hermes_config.example.csv` has one row per setup:

| `profile_id` | Workflow | Roles |
|---|---|---|
| `default` | `AHAWR_v13.json` (Hermes) | OpenRouter Architect/Reviewer, local llama.cpp Worker |
| `claude-code` (enabled) | `AHAWR_v13_ClaudeCode.json` | all roles on Anthropic |
| `claude-code` (disabled) | same | Worker on the local model through LiteLLM (`worker_provider=local`) |
| `claude-code` (disabled) | same | all roles on OpenAI (`provider=openai`); needs the Codex executor of mission `ahawr-codex-executor`, not in claude-runner yet |

The Claude Code workflow uses the first enabled `claude-code` row: enable exactly one of the three.
The Hermes workflow uses the first enabled row among `default`, `test`, `openrouter`.

Keep your own copies (exports of the live tables, mission CSVs, run exports) in `data-tables/`:
it is gitignored.

Verify that:

- `hermes_config` contains an enabled `default` profile;
- `agent_prompts` contains enabled prompts;
- `missions` contains at least one enabled mission.

#### 9. Check `state_namespace`

Every mission used by AHAWR must have a non-empty:

```text
state_namespace
```

Example:

```text
mission_id: simple-readonly-review
state_namespace: test
```

The namespace isolates persistent state between missions.

#### 10. Import `AHAWR_v13.json` or `AHAWR_v13_ClaudeCode.json`

Import the workflow for your variant:

```text
AHAWR_v13.json               # Hermes
AHAWR_v13_ClaudeCode.json    # Claude Code (claude-runner)
```

After importing, open the imported Run Manager and copy its workflow id from the URL (`/workflow/<id>`). If it is not `nhjwX1G7FiVTO2Ah`, put it into the `run_manager_workflow_id` column of `hermes_config`. `Architect/Worker/Reviewer Start` take the id from there. Their inputs are built by the `Build … Run Input` Code nodes, so re-selecting or re-importing the Run Manager never resets them.

If n8n assigns a different workflow ID after import, update the corresponding references.

#### 11. Configure credentials

Configure Hermes/API credentials in n8n Credentials or environment variables.

Never commit:

```text
.env
API keys
Bearer tokens
passwords
real credentials
```

#### 12. Run the first test mission

Start with a small read-only mission:

```text
Read the specified file.
Do not modify any files.
Do not execute commands that modify files.
Return a short summary.
```

Verify the complete pipeline:

```text
Mission
   ↓
Architect
   ↓
Worker
   ↓
Reviewer
   ↓
Approved
```

Only after this basic flow works should you use larger development missions.

### How to use it

The normal usage pattern is:

```text
1. Add/edit a mission in `missions`
2. Choose models/providers in `hermes_config`
3. Update prompts in `agent_prompts` when necessary
4. Start AHAWR
5. Monitor the n8n execution
6. Inspect Worker and Reviewer results
7. Review the final `approved` or failure output
```

For example, changing the Worker model should normally require changing:

```text
hermes_config.worker_model
hermes_config.worker_provider
```

rather than editing dozens of nodes in the workflow.

Changing the mission should normally require changing the row in:

```text
missions
```

rather than changing the orchestration logic.

### Recommended operational workflow

For development:

```text
Git working tree
      │
      ▼
edit workflow / CSV
      │
      ▼
test in n8n
      │
      ▼
inspect execution
      │
      ▼
git diff
      │
      ▼
commit
```

Keep the workflow JSON, prompt definitions, and non-secret configuration under version control.

Keep runtime data, credentials, local model files, and temporary state outside Git.

### Context Retrieval Layer (AHAWR v13)

`retrieval-service/` is an optional, read-only Context Retrieval Layer that runs as its own container, `ahawr-retrieval`, inside the n8n compose stack (`docker compose up` starts both; n8n reaches it at `http://ahawr-retrieval:8500`, no host port). It gives the Worker and the Reviewer relevant, current, provenance-aware context from repository code and project documentation. It does not take over orchestration (n8n), execution (Hermes) or persistent state (Data Tables), and it is never used for recovery.

- Hybrid retrieval: BM25 + vectors + symbol search, reciprocal-rank fusion, text-only cross-encoder reranking through `reranker-service`, deterministic filters and ranking. Built-in CPU models (no GPU, baked into the image, opt-in): the multilingual-e5-small embedder, which clearly helps on Russian task text, and a small cross-encoder.
- Separate Worker and Reviewer retrieval profiles.
- Every chunk carries provenance: source type, path, symbol/section, chunk id, content hash, snapshot/version, scores and rank.
- Incremental indexing with chunk-level invalidation, and a retrieval cache with semantic query fingerprints for retries.
- Its own local index by default; `rag-platform` can be attached as an external backend, with automatic fallback to the local index.
- An Eval Harness (gold/silver datasets, retrieval, system and AHAWR-level metrics) decides on every later extension.
- Agents can also search during a task: `ahawr-search "query"` in the claude-runner image queries the folder it runs in (fail-open, 1500-token budget). The Reviewer's context is focused on the files the Worker changed (`changed_paths`), and `ahawr-retrieval usage` reports per-profile precision, recall and search calls from the retrieval log and the runner events.

`AHAWR_v13.json` calls `POST http://ahawr-retrieval:8500/retrieve` before `Worker Start` and `Reviewer Start`. The call is fail-open, and retrieval is off unless `hermes_config.retrieval_enabled` is `true`. With retrieval off, v13 behaves like v12. Set `AHAWR_WORKSPACE_DIR` and the optional `RETRIEVAL_*` values in `.env` (see `.env.example`); the workspace is mounted read-only into the retrieval container at `/workspace`. See [`docs/retrieval/`](docs/retrieval/ARCHITECTURE.md), [`retrieval-service/README.md`](retrieval-service/README.md) and [`docs/retrieval/INTEGRATION.md`](docs/retrieval/INTEGRATION.md).

**Turning retrieval on (checklist).**
1. In the `hermes_config` row the workflow uses (`claude-code` for AHAWR v13 — Claude Code, the Hermes profile row for `AHAWR_v13.json`), set `retrieval_enabled = true`. It is `false` by default, and then no request is sent at all.
2. Give the mission a `working_directory` (for example `D:\ClaudeProjects\app`). Its corpus is created from that folder on the first request. Without it, the `retrieval_corpora_json` corpora of `hermes_config` are used (`ahawr-workspace` = `/workspace`).
3. Import the current workflow (older imports do not send `corpus_roots`).
4. Check it:
   * the dashboard (`http://localhost:8701`) shows a **RAG** row under the Worker's and the Reviewer's prompt and `RAG N chunks` in the summary; `RAG none` means no context arrived;
   * `docker exec n8n-autonomous-agents wget -qO- http://ahawr-retrieval:8500/corpora` lists the mission's corpus;
   * when retrieval is on but the context is missing, the n8n execution shows the reason in `retrieval_worker_status` / `retrieval_worker_error` (node *Attach Worker Context*).

The Architect gets no retrieved context; only the Worker and the Reviewer do.

### AHAWR on Claude Code (`claude-runner`)

`AHAWR_v13_ClaudeCode.json` is AHAWR v13 with **Claude Code instead of Hermes** as the execution engine. Claude Code has no HTTP run API of its own. `claude-runner/` runs the Claude Code CLI headless behind the same `/v1/runs` contract as the Hermes gateway, so orchestration, Data Table state, retries and recovery stay the same.

- Container `claude-runner` in the same compose stack: `docker compose up -d --build` builds and starts it together with n8n, `ahawr-retrieval` and `litellm`. n8n reaches it at `http://claude-runner:8700`; the run API has no host port.
- `Claude_Code_Run_Manager_v1.json` is `Hermes_Run_Manager_v5.json` with the runner endpoints and a role field. The Hermes TUI/WebSocket compression is replaced by `POST /v1/sessions/{id}/compact`, which runs Claude Code `/compact` only above a context threshold.
- Sessions are resumable Claude Code sessions (`--resume`), kept in a volume. A run interrupted by a runner restart is reported as `run_not_found`, so the Run Manager continues the saved session.
- Per-role permissions:
  - Worker: `bypassPermissions`, but it cannot read `.env` and cannot `git commit` or `git push`;
  - Architect and Reviewer: read-only.
- Model access: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), or Bedrock/Vertex/Foundry.
- Projects: `missions.working_directory` (e.g. `D:\OpenAIProjects\app`) is where Claude Code runs and what retrieval indexes for that mission. The drive is mounted into the containers (`AHAWR_HOST_DRIVE`, default `D:\`).
- Providers per role, as with Hermes: roles with `*_provider=anthropic` go to Anthropic, and a role with `*_provider=local` goes through the `litellm` container to a local OpenAI-compatible llama.cpp server. See `litellm/config.yaml` and the `CLAUDE_RUNNER_PROVIDER_LOCAL__*` block of `.env.example`.
- Configuration: the `claude-code` row and the `runner_url` column of `hermes_config`. Models are `opus` / `sonnet` / `haiku` or full model names.
- Dashboard at `http://localhost:8701` (container `ahawr-dashboard`; read-only, 127.0.0.1 only). It shows the runs of every role in both variants: Claude Code runs and Hermes sessions (`HERMES_API_URL`, `HERMES_API_KEY`). The layout follows DeepSeek Harness' Trajectory view: a timeline, a colored ledger with thinking, tool calls and their results, steps with TTFT, generation time, tokens and tok/s, and an inspector for each record.

**Local Worker model (llama.cpp through LiteLLM).** Recommended settings for a 64K-token slot (the `CLAUDE_RUNNER_PROVIDER_LOCAL__*` block of `.env.example`):

- `CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536` (match `llama-server -c`), `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`. Claude Code then auto-compacts at 65536 − 8192 − 13000 = 44344 tokens. The runner refuses to start if the trigger leaves less than 20000 tokens of headroom.
- The window follows llama-server. Before each local run the runner reads the model's `--ctx-size` from llama-server (`GET /v1/models`; `CONTEXT_PROBE_URL`, set in `docker-compose.yml` to `http://host.docker.internal:8033` with the key `LLAMACPP_API_KEY`) and sets `CLAUDE_CODE_MAX_CONTEXT_TOKENS` and `CLAUDE_CODE_AUTO_COMPACT_WINDOW` to it. The autocompact trigger is a percentage of that window: `COMPACT_PCT` (e.g. `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT=70`), else `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, else the static trigger's share (44344 / 65536 = 67.66%, so a 64K server compacts where it did before and a 96K one at 66516). It never goes above the window minus room for one reply and one large tool output (a 32K server compacts at 11576). The run's event log has a `context_window` record. When llama-server does not answer, the static values above apply.
- `COMPACT_MIN_TOKENS=40000`: the runner's `/compact` before resuming a session, below Claude Code's own trigger. With the probe it scales with the window like the trigger (40000 / 65536 = 61%: 60000 at 96K), or `COMPACT_MIN_PCT` sets the percentage; it never exceeds the autocompact trigger.
- `BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`: one tool output cannot fill the window.
- `CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS=1` (default): a PreCompact hook asks for a short summary (1500–2000 tokens in fixed sections) instead of Claude Code's ~8K-token one. `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS` bounds a compaction.
- `TOOLS=Bash,Read,Edit,Write,Glob,Grep` cuts the system prompt from ~15K to ~4K tokens.

**Retries.** A `needs_changes` retry continues the Worker's session and hands it its previous report and the review. A context overflow forces a compaction before the retry. A failed task resumes where it stopped, and when compaction fails a fresh session gets the old session's digest. The attempt counter takes the highest attempt logged for the task, so `max_attempts_per_task` holds even if `task_attempt` is reset by hand.

**Cost.** The dashboard shows Claude Code's own estimate per run (the run's share, not the session total). The image prices the local model like Claude Sonnet 5 through `claude-runner/managed-settings.json`, as a comparison with an all-Anthropic setup.

**Worker tools.** The image has git, pytest, ruff and mypy; build tools can be added with `CLAUDE_RUNNER_EXTRA_APT_PACKAGES` (e.g. `patch build-essential cmake`). The Worker cannot read `.env` files (every variant except `.env.example`), `git commit` or `git push`, and its prompt forbids printing environment variables.

The Hermes workflows are unchanged, and both variants can be imported side by side. See [`claude-runner/README.md`](claude-runner/README.md), [`docs/compaction-analysis.md`](docs/compaction-analysis.md) and [`CHANGELOG.md`](CHANGELOG.md).

### Versioning

The stack is versioned as a whole by the `claude-runner` package version (`claude-runner/pyproject.toml`, also reported by the runner API); the workflow files keep their own names (`AHAWR_v13`, `Claude_Code_Run_Manager_v1`, `Hermes_Run_Manager_v5`). **0.2.0** (2026-10-06) is the first release after 0.1.0 (the initial claude-runner): it adds the English-first working language with a mission report in the mission's language, compaction tuned to the local model's window, on-demand `ahawr-search` with a search-first hook, the read-only Reviewer shell, the fresh-session retry switch, verdict retry, attempt limits and operator closure of tasks, and shorter Telegram notices. Details are in [`CHANGELOG.md`](CHANGELOG.md).

### Why it works this way: constraints and workarounds

Several parts of this stack look odd until you know what they work around. The Worker is usually a small quantized local model (Qwen3.8-27B at 2-bit, MTP speculative decoding, one 12 GB GPU, a 64K–96K context window, roughly 25–30 tokens per second), while the Architect and the Reviewer run on Opus (about 130 tokens per second). Three facts drive most decisions: **the window is small**, **generation and uncached prefill are slow** (a full 78K-token prefill takes about 4 minutes, a compaction about 7), and **a cheap Worker is only useful if something checks its work**. Each item below says what the workaround is, why it exists, and what it costs.

**1. The mission and the plan are translated into English first; the report comes back in the mission's language.**
- *What.* The Architect writes the summary and every task in English whatever language the mission is written in. The Worker writes notes, reports and code comments in English and the Reviewer answers in English. Texts that must appear exactly as given (paths, identifiers, commands, UI strings, headings the mission requires) stay verbatim in the original language and in quotes. For a non-English mission the Architect adds a last task, `Mission report (<language>)`, that depends on all others; the Worker writes a report of the whole mission in the mission's language without changing files, the Reviewer checks language, coverage and facts, and the `✅ WORKFLOW APPROVED` Telegram notice carries that report.
- *Why.* The same text takes 5–15% fewer tokens in English (measured on one task: Qwen 490 → 416, Claude 753 → 675), which matters when the window is 64K and every retry resends the task. The small local model also follows rules written in English more reliably when the task, its own notes and the files are in one language; with a Russian task and English system rules it mixed both. The final report in the mission's language is there because the operator reads the result in their own language; making it a separate last task keeps the working language English for everything the models read during the run.
- *Cost.* One extra task per mission, and a translation step that can lose meaning. That is why anything that must match exactly is kept verbatim and quoted, and why the Reviewer checks the report against the facts. Prompts: `agent_prompts.example.csv` (the `LANGUAGE:` blocks).

**2. Compaction is short, early and sized to the model's real window.**
- *What.* A PreCompact hook asks the local model for a 1500–2000-token summary in fixed sections (Task, Findings, Changed files, Checks, Next step). Before each local run the runner reads `--ctx-size` from llama-server and sets the context window and the auto-compact trigger as a percentage of it; the runner's own `/compact` before a retry scales the same way. Tool output is capped (`BASH_MAX_OUTPUT_LENGTH`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`) and the tool list is trimmed, which cuts the system prompt from about 15K to about 4K tokens. The runner refuses to start if the trigger leaves less than 20000 tokens of headroom.
- *Why.* Claude Code's default summary is about 8K tokens, which took about 6 minutes at local generation speed; a fixed 40000-token pre-resume threshold compacted a 96K Worker at 46–52K, so the compaction took longer than the retry it preceded. A window that is changed on the server but not in the runner makes the model fail with an overflow instead of compacting.
- *Cost / open problem.* Every compaction still costs minutes, because the llama-server prompt cache is not reused for the compaction request (a full re-prefill of the prefix). The cause is not found yet; see "Known open problems" below.

**3. Retries, attempt limits and the operator.**
- *What.* A `needs_changes` retry resumes the Worker's session (`--resume`) and hands it its previous full report and the review; the Reviewer checks that parts it accepted earlier are still there. The retry counter takes the highest attempt logged for the task, and a task stops at `max_attempts_per_task` instead of retrying forever. A context overflow forces a compaction before the retry; if compaction fails, a fresh session gets the old session's digest (`GET /v1/sessions/{id}/digest`). `CLAUDE_RUNNER_RETRY_FRESH_SESSION=1` (off by default) starts a fresh session on a review retry instead of compacting the old one; the retry input is the task, the review, the previous report and the digest, with a character budget.
- *Why.* The local model sometimes cannot fix a review finding in three attempts, and a Reviewer can keep finding new issues in each attempt. An unbounded loop burns hours of GPU time, so the limit is hard.
- *How a failed task is handled.* The operator reads the last review, fixes or accepts the result by hand, and closes the task: the history gets an `operator_accept` row (not a Reviewer verdict) and the mission moves on. The record keeps the operator's closure visible, so statistics do not count it as a Reviewer pass.
- *The Reviewer's verdict is parsed leniently.* A local Reviewer wrote unescaped double quotes inside `reason` and failed the run; `Parse Review` now reads such a verdict leniently and, with no verdict at all, asks the same session again for the JSON only (up to `reviewer_max_retries`).

**4. Agents are made to search first, by a hook, not by a prompt.**
- *What.* `ahawr-search "<question>"` in the claude-runner image asks the retrieval layer for the most relevant fragments of the folder the agent works in. With `SEARCH_FIRST` a PreToolUse hook refuses exploratory `grep`/`rg`/`find`/`Grep`/`Glob` until the session has used `ahawr-search`, and reminds the agent every few searches afterwards. The command is fail-open (exit 0 when the service is down), says why it failed (rejected request, service error, unreachable, timeout), accepts the usual spellings of `--k`/`--budget`, and clamps the budget.
- *Why.* A prompt rule ("MANDATORY: search with ahawr-search first") was followed in 4 of 26 local Worker runs, and only early in the run, against 413 `grep` commands. Retrieval context at the start of a task is also not enough: the Worker's file-level precision was 46% and recall 25%, and one 3408-line patch file took 44.8% of the context tokens. So retrieval is on demand, the Reviewer's context is focused on the files the Worker changed, and large `.patch` files are demoted.
- *Cost.* A search costs a few seconds, and the first search of a new folder indexes it inside the request (8–9 s for 30 small files, hence the 30 s default timeout).

**5. The Reviewer can read, but not write.**
- *What.* The Reviewer profile runs in `dontAsk` mode with a read-only Bash allow-list (`ahawr-search`, `grep`, `ls`, `wc`, `head`, `tail`, `cat`, `diff`, `bash -n`, read-only `git`), and denies redirections to files and `git … --output`. `CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS` replaces the list.
- *Why.* In `dontAsk` a command runs only if a rule allows it; one local mission logged 105 denied Bash calls from its Reviewers, which then judged reports they could not check.
- *Result placement.* The Reviewer cannot see `/tmp` of the Worker. The optional result placement rule asks agents to put files the Reviewer must verify into the mission working directory and to cite the exact path in the report (`docs/ahawr-cheaper-retries.md` §5.3).

**6. What the Reviewer cannot verify is accepted by the operator.**
The Reviewer judges the Worker's report and the files it can read. The Worker cannot run Docker, build a GPU image or run a live service, so a mission that needs those can pass review and still not work (a patch series once passed all reviews while it did not apply; a rebuilt image then crashed on the first request). For such missions the acceptance criteria say what the operator runs (for example `docker build` and a benchmark), and the mission is not considered done until that has passed. A change made by hand in a working tree must also end up in the artifact (the patch, the file under version control), not only in the tree; reviews cannot see the difference.

**7. Cost numbers are estimates.**
Claude Code reports a cost for every run. For the local model the image prices it like Claude Sonnet 5 (`claude-runner/managed-settings.json`) so that a local run can be compared with an all-Anthropic one; this is not a bill. Real spending is the Anthropic roles (usually the Opus Reviewer and Architect). The run's `cost_usd` is its own share: Claude Code restores the session's total on `--resume`, so the runner subtracts the previous run's total.

**8. Telegram notices are short and safe.**
Task and review notices carry the task id, the attempt, the score and the reason cut at a sentence, not the whole review. The text is cut first and HTML-escaped after, because escaping first and cutting second can split an entity and make Telegram reject the message.

**9. Operational details that are easy to miss.**
- The image sets `core.autocrlf=true`, so a Windows checkout mounted into the container does not show every CRLF file as modified (85 false changes once confused a scope check).
- Runs may last up to `CLAUDE_RUNNER_MAX_RUN_SECONDS` (10800 in this stack's `.env`); evaluation-heavy Worker runs hit two hours.
- Live Data Table exports and mission files are kept out of git (`data-tables/`); the repository ships `hermes_config.example.csv`, `agent_prompts.example.csv` and `missions.example.csv`.
- Data Tables live in n8n's SQLite file; editing them directly (for example to close a task or to load a mission) is only safe while n8n is stopped, with a copy of the database made first. Prefer the n8n UI where it is enough.
- Worker and Reviewer cannot read `.env` files (every variant except `.env.example`) and cannot `git commit` or `git push`; the operator commits.

**Known open problems.**
- The llama-server prompt cache is not reused when Claude Code sends a compaction request, so each compaction re-prefills the whole prefix (about 7 minutes at 78K tokens; seven of them in one task). The cause (server build, Claude Code version or cache settings) is not established.
- The retrieval usage metric reports 0 opened files on live data, because runner run summaries carry no mission or task id to join them with retrieval requests.
- The built-in hashing embedder ranks rare identifiers poorly.
- Quality, price and speed comparisons between the local model and Anthropic models are single runs; see `CHANGELOG.md` for measured numbers.

### Hermes session compression

The Run Manager includes a dedicated Hermes TUI WebSocket compression path for long-running sessions. Compression is performed by `hermes_compress.py` through the Hermes WebSocket endpoint rather than through `/v1/runs`.

The compression flow is:

```text
Hermes login
   ↓
WS ticket
   ↓
session.resume
   ↓
session.compress
   ↓
compression summary
```

The compression RPC receives the target `session_id`, a WS ticket, `keep_recent`, and a timeout. The model/provider are not passed by the n8n compression RPC itself; Hermes resolves the compression model from its own configuration.

For the current Hermes configuration, configure a dedicated compression model explicitly:

```yaml
auxiliary:
  compression:
    provider: openrouter
    model: openrouter/free
```

This keeps compression independent from the Architect/Worker/Reviewer model selection in `hermes_config`. The normal service model fields (`architect_model`, `worker_model`, `reviewer_model`) are not the compression model.

The current deployment runs the Hermes backend used by n8n on port `9119`, for example:

```bash
hermes serve --host 0.0.0.0 --port 9119
```

After changing `~/.hermes/config.yaml`, restart the running `hermes serve` process so the new configuration is loaded. n8n itself does not need to be restarted solely because the Hermes YAML changed. Verify the startup output includes:

```text
HERMES_BACKEND_READY port=9119
```

The compression result should report the effective model/provider under its runtime information. A healthy configuration should show the explicitly configured compression route instead of an obsolete provider-specific default.

The Run Manager also preserves role-specific session state (`architect_session_id`, `worker_session_id`, `reviewer_session_id`). Compression must receive the session selected by the compression-target path; it must not silently substitute the Architect session for a Worker or Reviewer session.

### Compression troubleshooting

If compression reports that no diagnostic output was returned, inspect the raw `stdout` from the Execute Command node. The wrapper may contain:

```text
__HERMES_EXIT__=...
__HERMES_STDOUT__
{JSON response from Hermes}
__HERMES_STDERR__
```

The JSON between `__HERMES_STDOUT__` and `__HERMES_STDERR__` is the actual Hermes response. A compression result with `status: aborted`, `compression_failed: true`, `removed: 0`, and an error such as `Model ... is not supported` indicates a model/provider configuration problem, not a session-resume or WebSocket transport failure.

### Troubleshooting

#### Mission has no `state_namespace`

Verify that the selected mission row contains:

```text
state_namespace
```

and that the value is not empty.

Also verify that the `Mission`/`Load Mission` nodes actually pass that field into the workflow context.

#### Hermes run never finishes

Check:

- Hermes Gateway health;
- network connectivity from the n8n container;
- model availability;
- polling interval;
- maximum polling count;
- provider errors.

#### Reviewer keeps requesting changes

Inspect:

- Worker output;
- Reviewer prompt;
- task acceptance criteria;
- verification requirements;
- task retry limit.

A Reviewer failure does not necessarily mean the infrastructure is broken; it can be a legitimate task-level rejection.

#### State from another mission is being reused

Check the mission's:

```text
state_namespace
```

It must be unique for independent missions.

### Design principles

The project intentionally follows these principles:

**Orchestration is separate from content.**  
The workflow controls execution; Data Tables hold configuration, prompts and missions.

**Secrets are separate from configuration.**  
Credentials are managed by n8n rather than Git-tracked files.

**Asynchronous execution is explicit.**  
Long-running model calls are handled through run IDs and polling.

**Tasks are independently reviewable.**  
The Reviewer evaluates the current task instead of judging the whole mission at once.

**State is persistent.**  
Long-running work survives transient workflow execution boundaries.

**Missions are data-driven.**  
Changing the mission should not require rewriting the orchestrator.

---

## Русская версия

### Что делает проект

Это система оркестрации автономных AI-агентов на базе **n8n**. Роли работают через **Hermes Gateway** или через **Claude Code** (`claude-runner`, с моделями Anthropic или локальной моделью llama.cpp), см. [AHAWR на Claude Code](#ahawr-на-claude-code-claude-runner). Последние изменения: [`CHANGELOG.md`](CHANGELOG.md).

Главный workflow — **Agentic Hub for Automation Workflow Routing (AHAWR)**.

Он разделяет работу на три логические роли:

```text
Architect / Planner
        │
        ▼
      Worker
        │
        ▼
     Reviewer
        │
   ┌────┴────┐
   │         │
 PASS    NEEDS_CHANGES
   │         │
   ▼         └──► повтор текущей задачи
следующая
 задача
```

Вместо одного большого запроса модель сначала строит план, затем Worker выполняет задачи по одной, а Reviewer независимо проверяет результат.

### Что происходит при запуске

AHAWR:

1. загружает mission;
2. загружает конфигурацию моделей и параметров;
3. загружает prompts;
4. передаёт миссию Architect/Planner;
5. получает структурированный план задач;
6. выбирает текущую задачу;
7. запускает Worker через Hermes;
8. ждёт завершения асинхронного запуска;
9. при необходимости делает повторные polling-запросы;
10. передаёт результат Worker в Reviewer;
11. получает `pass` или `needs_changes`;
12. либо переходит к следующей задаче, либо повторяет текущую;
13. сохраняет состояние выполнения;
14. после завершения всех задач формирует итоговый результат.

То есть n8n здесь выступает именно как **оркестратор**, а Hermes — как execution gateway для AI-run'ов.

### `AHAWR_v13.json`

Это основной workflow проекта.

Он отвечает за:

- загрузку конфигурации;
- загрузку prompts;
- загрузку mission;
- запуск Architect;
- разбор плана;
- выбор текущей задачи;
- запуск Worker;
- polling состояния Worker;
- запуск Reviewer;
- polling Reviewer;
- retry;
- сохранение состояния;
- восстановление состояния;
- переход между задачами;
- финальный результат.

### `Hermes_Run_Manager_v5.json`

Это переиспользуемый sub-workflow для работы с Hermes Gateway.

Он принимает параметры:

```text
role
run_id
session_id
input
resume_input
model
provider
poll_seconds
max_polls
max_retries
retry_count
retry_delays
```

и инкапсулирует низкоуровневую логику запуска Hermes, ожидания результата, polling и retry.

Благодаря этому основной AHAWR workflow не должен содержать всю HTTP-логику Hermes непосредственно в каждой роли.

### Архитектура

```text
                    n8n
                     │
             ┌───────┴────────┐
             │     AHAWR      │
             └───────┬────────┘
                     │
             ┌───────▼────────┐
             │ Architect       │
             │ планирование    │
             └───────┬────────┘
                     │
             ┌───────▼────────┐
             │ Worker          │
             │ выполнение      │
             └───────┬────────┘
                     │
                Hermes Gateway
                     │
             ┌───────▼────────┐
             │ Reviewer        │
             │ проверка        │
             └───────┬────────┘
                     │
              pass / retry
```

### Асинхронная работа Hermes

Worker и Reviewer не считаются одним синхронным HTTP-запросом.

Логика выглядит так:

```text
POST /v1/runs
      │
      ▼
    run_id
      │
      ▼
    wait
      │
      ▼
GET /v1/runs/{run_id}
      │
      ├── running → повторить polling
      │
      ├── completed → обработать результат
      │
      └── error → retry / failure
```

Это позволяет работать с большими локальными моделями и длительными задачами.

### Retry

В системе существуют два разных уровня retry.

**Provider retry** — повтор связи с provider/Hermes при временной ошибке.

**Task retry** — повтор конкретной задачи, если Reviewer вернул `needs_changes`.

Таким образом, неудача одной задачи не требует заново строить весь план.

Задержки retry хранятся как JSON, например:

```text
[15,30,60,120,300]
```

### Сохранение состояния

AHAWR хранит состояние выполнения в n8n Data Tables.

Ключом состояния является mission-specific:

```text
state_namespace
```

Например:

```text
mission_id: simple-readonly-review
state_namespace: test
```

Это позволяет различным миссиям иметь независимое состояние.

В сохранённом состоянии могут находиться:

- текущая задача;
- индекс задачи;
- количество попыток;
- run/session Architect;
- run/session Worker;
- run/session Reviewer;
- результат последнего review;
- прогресс mission.

### Data Tables

Проект использует три основные таблицы.

#### `hermes_config`

Хранит runtime-конфигурацию:

```text
profile_id
enabled
architect_model
architect_provider
worker_model
worker_provider
reviewer_model
reviewer_provider
max_tasks
max_attempts_per_task
architect_max_polls
worker_max_polls
reviewer_max_polls
architect_poll_seconds
worker_poll_seconds
reviewer_poll_seconds
architect_max_retries
worker_max_retries
reviewer_max_retries
retry_delays_json
```

Изменение модели или параметров выполнения обычно не требует изменения workflow.

#### `agent_prompts`

Хранит prompts для:

```text
Architect / Planner
Worker
Reviewer
```

Prompts отделены от workflow и могут изменяться независимо.

**Рабочий язык.** План, задачи, заметки и отчёты Worker'а и ревью пишутся на английском, на каком бы
языке ни была миссия: тот же текст на английском занимает на 5-15% меньше токенов у Qwen и Claude, а
Worker видит один язык вместо английских правил и переведённой задачи. Тексты, которые должны
появиться дословно (пути, идентификаторы, строки и заголовки, которых требует миссия), остаются на
исходном языке. Для миссии не на английском архитектор добавляет последнюю задачу
`Mission report (<язык>)`: Worker возвращает отчёт по всей миссии на языке миссии, ревьюер его
проверяет, и он приходит в Telegram-уведомлении `✅ WORKFLOW APPROVED`.

#### `missions`

Хранит миссии:

- `mission_id`;
- цель;
- правила;
- acceptance criteria;
- `state_namespace`.

Поэтому можно создавать различные сценарии работы без переписывания AHAWR.

### Где хранить секреты

Секреты **не должны** находиться в:

```text
*.example.csv
AHAWR_v13*.json
*_Run_Manager_*.json
```

Используйте:

- n8n Credentials;
- environment variables;
- внешний secret manager.

В Git не должны попадать:

```text
.env
*.sqlite
API keys
Bearer tokens
passwords
model weights
runtime data
logs
```

### Файлы репозитория

Состав репозитория:

```text
.
├── AHAWR_v13.json                   # AHAWR on Hermes (with the retrieval layer)
├── AHAWR_v13_ClaudeCode.json        # AHAWR on Claude Code (claude-runner)
├── Hermes_Run_Manager_v5.json       # run/poll/retry/compression sub-workflow for Hermes
├── Claude_Code_Run_Manager_v1.json  # the same for claude-runner
├── hermes_config.example.csv        # Data Table example: profiles for Hermes, Claude Code, local, OpenAI
├── agent_prompts.example.csv        # Data Table example: Architect / Worker / Reviewer prompts
├── missions.example.csv             # Data Table example: one mission
├── data-tables/                     # your live CSVs (gitignored): exports, mission files
├── claude-runner/                   # Claude Code CLI behind the /v1/runs API, dashboard
├── retrieval-service/               # ahawr-retrieval (Context Retrieval Layer)
├── litellm/                         # LiteLLM config and hook for the local llama.cpp model
├── docs/                            # retrieval docs, compaction analysis
├── scripts/                         # helper scripts
├── docker-compose.yml, Dockerfile   # the stack: n8n, claude-runner, ahawr-retrieval, litellm, dashboard
├── .env.example                     # every setting, with comments (copy to .env)
├── ARCHITECTURE_CONTRACT.md
├── CHANGELOG.md
└── README.md
```

Что и когда менялось: [`CHANGELOG.md`](CHANGELOG.md).

### Установка

#### 1. Установить Git

Установите Git:

https://git-scm.com/downloads

Проверьте:

```powershell
git --version
```

#### 2. Установить Docker Desktop

Установите Docker Desktop:

https://www.docker.com/products/docker-desktop/

В Windows запустите Docker Desktop и убедитесь, что Docker Engine работает.

Проверьте:

```powershell
docker --version
docker compose version
```

#### 3. Клонировать репозиторий

```powershell
git clone https://github.com/Sa1avatus/n8n-agent-orchestrator.git
cd n8n-agent-orchestrator
```

#### 4. Запустить n8n

После запуска Docker Desktop:

```powershell
docker compose up -d
```

Проверьте контейнеры:

```powershell
docker compose ps
```

Для просмотра логов:

```powershell
docker compose logs -f
```

Откройте n8n по порту, указанному в `docker-compose.yml`.

#### 5. Подготовить Hermes Gateway

Hermes должен быть запущен и доступен из контейнера n8n.

Для Docker Desktop сервис на хостовой машине обычно доступен через:

```text
http://host.docker.internal:8642
```

Используйте фактический адрес и порт вашей установки.

#### 6. Подготовить LLM backend

Hermes должен иметь доступ к provider, который используется AHAWR.

Например:

```text
n8n
 │
 ▼
Hermes Gateway
 │
 ├── local llama.cpp
 │
 └── external model provider
```

Модели и provider задаются через таблицу `hermes_config`.

#### 7. Импортировать Run Manager

В n8n:

1. Импортируйте `Hermes_Run_Manager_v5.json` (вариант с Hermes) или `Claude_Code_Run_Manager_v1.json` (вариант с Claude Code, см. [AHAWR на Claude Code](#ahawr-на-claude-code-claude-runner)).
2. Настройте необходимые Hermes credentials.
3. Выполните простой тестовый запрос.
4. Убедитесь, что Hermes запускает run и возвращает результат.

Не помещайте реальные credentials в Git-tracked workflow-файлы.

#### 8. Создать Data Tables

Создайте:

```text
hermes_config
agent_prompts
missions
```

Импортируйте примеры из корня репозитория: `hermes_config.example.csv`,
`agent_prompts.example.csv`, `missions.example.csv` (в `hermes_config` и `missions` разделитель `;`).

В `hermes_config.example.csv` по строке на вариант:

| `profile_id` | Workflow | Роли |
|---|---|---|
| `default` | `AHAWR_v13.json` (Hermes) | Architect/Reviewer через OpenRouter, Worker — локальный llama.cpp |
| `claude-code` (включена) | `AHAWR_v13_ClaudeCode.json` | все роли на Anthropic |
| `claude-code` (выключена) | тот же | Worker на локальной модели через LiteLLM (`worker_provider=local`) |
| `claude-code` (выключена) | тот же | все роли на OpenAI (`provider=openai`); нужен исполнитель Codex из миссии `ahawr-codex-executor`, в claude-runner его пока нет |

Workflow на Claude Code берёт первую включённую строку `claude-code`: включите ровно одну из трёх.
Workflow на Hermes берёт первую включённую из `default`, `test`, `openrouter`.

Свои копии (выгрузки живых таблиц, CSV миссий, экспорты прогонов) держите в `data-tables/`:
папка в .gitignore.

Проверьте, что:

- в `hermes_config` есть enabled-профиль `default`;
- в `agent_prompts` есть enabled prompts;
- в `missions` есть хотя бы одна enabled mission.

#### 9. Проверить `state_namespace`

У каждой mission, которую использует AHAWR, должен быть непустой:

```text
state_namespace
```

Например:

```text
mission_id: simple-readonly-review
state_namespace: test
```

`state_namespace` разделяет сохранённое состояние разных missions.

#### 10. Импортировать `AHAWR_v13.json` или `AHAWR_v13_ClaudeCode.json`

Импортируйте workflow своего варианта:

```text
AHAWR_v13.json               # Hermes
AHAWR_v13_ClaudeCode.json    # Claude Code (claude-runner)
```

После импорта откройте импортированный Run Manager и скопируйте его ID из адреса (`/workflow/<id>`). Если ID не `nhjwX1G7FiVTO2Ah`, впишите его в колонку `run_manager_workflow_id` таблицы `hermes_config`. `Architect/Worker/Reviewer Start` берут ID оттуда. Входы для них собирают Code-узлы `Build … Run Input`, поэтому повторный выбор или переимпорт Run Manager их не сбрасывает.

Если после импорта n8n назначил другой workflow ID, обновите соответствующие ссылки.

#### 11. Настроить credentials

Настройте Hermes/API credentials через n8n Credentials или environment variables.

Никогда не коммитьте в Git:

```text
.env
API keys
Bearer tokens
passwords
real credentials
```

#### 12. Выполнить первый тест

Начните с небольшой read-only mission:

```text
Прочитать указанный файл.
Ничего не изменять.
Не выполнять команды, изменяющие файлы.
Вернуть краткое резюме.
```

Проверьте полную цепочку:

```text
Mission
   ↓
Architect
   ↓
Worker
   ↓
Reviewer
   ↓
Approved
```

Только после успешного прохождения этой цепочки используйте большие development missions.

### Как пользоваться

Обычный рабочий цикл:

```text
1. Добавить или изменить mission
2. Выбрать модели в hermes_config
3. При необходимости изменить prompts
4. Запустить AHAWR
5. Следить за execution в n8n
6. Проверить Worker result
7. Проверить Reviewer result
8. Получить финальный approved/failure результат
```

Для смены Worker-модели достаточно изменить:

```text
worker_model
worker_provider
```

в `hermes_config`.

Для смены задачи достаточно изменить соответствующую запись в `missions`.

### Context Retrieval Layer (AHAWR v13)

`retrieval-service/` — опциональный read-only Context Retrieval Layer. Он работает в собственном контейнере `ahawr-retrieval` внутри compose-стека n8n: `docker compose up` поднимает оба контейнера, n8n обращается к нему по адресу `http://ahawr-retrieval:8500`, наружу порт не публикуется. Он даёт Worker и Reviewer релевантный, актуальный контекст из кода репозитория и документации проекта, с provenance для каждого фрагмента. Сервис не заменяет orchestration (n8n), выполнение (Hermes) и persistent state (Data Tables) и никогда не используется для восстановления состояния.

- Hybrid retrieval: BM25 + векторы + поиск по символам, RRF, text-only cross-encoder через `reranker-service`, детерминированные фильтры и ранжирование. Встроенные CPU-модели (без GPU, запечены в образ, включаются явно): эмбеддер multilingual-e5-small, который заметно помогает на русских формулировках задач, и небольшой cross-encoder.
- Отдельные профили Worker и Reviewer.
- У каждого чанка есть provenance: тип источника, путь, символ/секция, chunk id, content hash, snapshot/version, scores и rank.
- Инкрементальная индексация, инвалидация на уровне чанков, кэш с семантическим fingerprint для повторных попыток.
- По умолчанию используется собственный локальный индекс. `rag-platform` можно подключить как внешний бэкенд; если он недоступен, сервис автоматически переходит на локальный индекс.
- Все дальнейшие расширения принимаются по данным Eval Harness (gold/silver наборы; метрики retrieval, системы и AHAWR).
- Агенты могут искать и по ходу задачи: команда `ahawr-search "запрос"` в образе claude-runner ищет по папке, из которой вызвана (fail-open, бюджет 1500 токенов). Контекст Reviewer'а фокусируется на файлах, изменённых Worker'ом (`changed_paths`), а `ahawr-retrieval usage` показывает по профилям точность, полноту и число поисков по журналу retrieval и событиям runner'а.

`AHAWR_v13.json` вызывает `POST http://ahawr-retrieval:8500/retrieve` перед `Worker Start` и `Reviewer Start`. Ошибка или таймаут не останавливают задачу. Путь к workspace и параметры `RETRIEVAL_*` задаются в `.env` (см. `.env.example`); workspace монтируется в контейнер retrieval только на чтение в `/workspace`. Retrieval выключен, пока в `hermes_config` не задано `retrieval_enabled = true`; в этом режиме v13 работает как v12. Подробности: [`docs/retrieval/`](docs/retrieval/ARCHITECTURE.md), [`docs/retrieval/INTEGRATION.md`](docs/retrieval/INTEGRATION.md).

**Как включить retrieval (чек-лист).**
1. В строке `hermes_config`, которую использует воркфлоу, поставь `retrieval_enabled = true`. Для AHAWR v13 — Claude Code это строка `claude-code`, для `AHAWR_v13.json` — строка профиля Hermes. По умолчанию там `false`, и тогда запросы в RAG вообще не отправляются.
2. Заполни у миссии `working_directory` (например, `D:\ClaudeProjects\app`). Корпус создаётся из этой папки при первом запросе. Без неё используются корпуса из `retrieval_corpora_json` в `hermes_config` (`ahawr-workspace` = `/workspace`).
3. Импортируй текущую версию воркфлоу: старые импорты не передают `corpus_roots`.
4. Проверь:
   * в дашборде (`http://localhost:8701`) под промптом Worker'а и Reviewer'а есть строка **RAG**, а в сводке «RAG N chunks». «RAG none» значит, что контекст не пришёл;
   * `docker exec n8n-autonomous-agents wget -qO- http://ahawr-retrieval:8500/corpora` показывает корпус миссии;
   * если retrieval включён, а контекста нет, причина видна в исполнении n8n в полях `retrieval_worker_status` / `retrieval_worker_error` (узел *Attach Worker Context*).

Architect контекст из RAG не получает, только Worker и Reviewer.


### AHAWR на Claude Code (`claude-runner`)

`AHAWR_v13_ClaudeCode.json` — это AHAWR v13, в котором вместо Hermes работает **Claude Code**. Своего HTTP API для запусков у Claude Code нет. `claude-runner/` запускает Claude Code CLI без интерактива и отдаёт тот же контракт `/v1/runs`, что и Hermes Gateway. Поэтому оркестрация, состояние в Data Tables, ретраи и восстановление не меняются.

- Контейнер `claude-runner` входит в тот же compose-стек: `docker compose up -d --build` собирает и запускает его вместе с n8n, `ahawr-retrieval` и `litellm`. n8n обращается к нему по адресу `http://claude-runner:8700`.
- `Claude_Code_Run_Manager_v1.json` — это `Hermes_Run_Manager_v5.json` с адресами runner. Компрессия через Hermes WebSocket заменена на `POST /v1/sessions/{id}/compact`: это `/compact` Claude Code, который запускается только при большом контексте.
- Сессии Claude Code продолжаются через `--resume` и хранятся в томе. Прогон, прерванный рестартом runner, возвращает `run_not_found`, и Run Manager продолжает сессию.
- Права по ролям:
  - Worker: `bypassPermissions`, но без чтения `.env`, `git commit` и `git push`;
  - Architect и Reviewer: только чтение.
- Доступ к модели: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (из `claude setup-token`) или Bedrock/Vertex/Foundry.
- Проекты: в `missions.working_directory` (например, `D:\OpenAIProjects\app`) указывается папка, в которой работает Claude Code и которую индексирует retrieval для этой миссии. Диск подключается в контейнеры через `AHAWR_HOST_DRIVE` (по умолчанию `D:\`).
- Провайдеры по ролям, как в Hermes: роли с `*_provider=anthropic` идут в Anthropic, а роль с `*_provider=local` — через контейнер `litellm` в локальный llama.cpp. См. `litellm/config.yaml` и блок `CLAUDE_RUNNER_PROVIDER_LOCAL__*` в `.env.example`.
- Настройки: строка `claude-code` и колонка `runner_url` в `hermes_config`.
- Дашборд `http://localhost:8701` (контейнер `ahawr-dashboard`, только чтение, только 127.0.0.1). Показывает прогоны всех ролей обоих вариантов: Claude Code и сессии Hermes (`HERMES_API_URL`, `HERMES_API_KEY`). Устроен как вкладка Trajectory в DeepSeek Harness: таймлайн, цветной журнал с размышлениями, тулами и их результатами, шаги с TTFT, временем генерации, токенами и ток/с, инспектор записи.

**Локальная модель Worker'а (llama.cpp через LiteLLM).** Рекомендуемые настройки для слота на 64K токенов (блок `CLAUDE_RUNNER_PROVIDER_LOCAL__*` в `.env.example`):

- `CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536` (как `llama-server -c`), `CLAUDE_CODE_MAX_OUTPUT_TOKENS=8192`. Автосжатие Claude Code срабатывает на 65536 − 8192 − 13000 = 44344 токенах. Runner не запустится, если до конца окна остаётся меньше 20000 токенов запаса.
- Окно следует за llama-server. Перед каждым локальным прогоном runner читает у llama-server `--ctx-size` модели (`GET /v1/models`; `CONTEXT_PROBE_URL`, в `docker-compose.yml` это `http://host.docker.internal:8033` с ключом `LLAMACPP_API_KEY`) и выставляет по нему `CLAUDE_CODE_MAX_CONTEXT_TOKENS` и `CLAUDE_CODE_AUTO_COMPACT_WINDOW`. Порог компакции — процент от этого окна: `COMPACT_PCT` (например `CLAUDE_RUNNER_PROVIDER_LOCAL__COMPACT_PCT=70`), иначе `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`, иначе доля статичного порога (44344 / 65536 = 67.66%: сервер на 64K сжимает там же, где раньше, на 96K — на 66516). Порог не выше окна минус запас на один ответ и один большой вывод инструмента (на 32K — 11576). В журнале прогона появляется запись `context_window`. Если llama-server не отвечает, действуют статичные значения выше.
- `COMPACT_MIN_TOKENS=40000`: `/compact` runner'а перед продолжением сессии, ниже порога самого Claude Code. С проверкой окна он масштабируется вместе с окном, как и порог автокомпакции (40000 / 65536 = 61%: на 96K это 60000), или процент задаётся `COMPACT_MIN_PCT`; выше порога автокомпакции он не поднимается.
- `BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`: один вывод инструмента не заполнит окно.
- `CLAUDE_RUNNER_LOCAL_COMPACT_INSTRUCTIONS=1` (по умолчанию): хук PreCompact просит короткую сводку (1500–2000 токенов по фиксированным разделам) вместо ~8K токенов у Claude Code. `CLAUDE_RUNNER_COMPACT_TIMEOUT_SECONDS` ограничивает время сжатия.
- `TOOLS=Bash,Read,Edit,Write,Glob,Grep` сокращает системный промпт с ~15K до ~4K токенов.

**Ретраи.** Ретрай после `needs_changes` продолжает сессию Worker'а и передаёт ему его прошлый отчёт и ревью. Переполнение контекста принудительно запускает сжатие перед ретраем. Упавшая задача продолжается с места остановки. Если сжатие не удалось, новая сессия получает дайджест старой. Счётчик попыток берёт наибольшую попытку из журнала задачи, поэтому `max_attempts_per_task` соблюдается, даже если `task_attempt` сброшен вручную.

**Стоимость.** Дашборд показывает оценку самого Claude Code для каждого прогона: долю этого прогона, а не итог сессии. Образ оценивает локальную модель по ценам Claude Sonnet 5 через `claude-runner/managed-settings.json`, чтобы сравнивать с вариантом целиком на Anthropic.

**Инструменты Worker'а.** В образе есть git, pytest, ruff и mypy. Инструменты сборки добавляются через `CLAUDE_RUNNER_EXTRA_APT_PACKAGES` (например, `patch build-essential cmake`). Worker не может читать файлы `.env` (любые варианты, кроме `.env.example`), делать `git commit` и `git push`, а промпт запрещает ему выводить переменные окружения.

Воркфлоу для Hermes не изменены, обе версии можно импортировать одновременно. Подробности в [`claude-runner/README.md`](claude-runner/README.md), [`docs/compaction-analysis.md`](docs/compaction-analysis.md) и [`CHANGELOG.md`](CHANGELOG.md).
### Версии

Стек версионируется по версии пакета `claude-runner` (`claude-runner/pyproject.toml`, её же отдаёт API runner'а); файлы воркфлоу сохраняют свои имена (`AHAWR_v13`, `Claude_Code_Run_Manager_v1`, `Hermes_Run_Manager_v5`). **0.2.0** (2026-10-06) — первый релиз после 0.1.0 (первый claude-runner): рабочий язык «сначала английский» с отчётом по миссии на языке миссии, сжатие под окно локальной модели, поиск `ahawr-search` по требованию с хуком «сначала поиск», Reviewer с оболочкой только для чтения, переключатель ретрая в свежей сессии, повтор вердикта, лимит попыток с закрытием задач оператором и более короткие Telegram-уведомления. Подробности — в [`CHANGELOG.md`](CHANGELOG.md).

### Почему всё устроено именно так: ограничения и обходные решения

Многое в этом стеке выглядит странно, пока не знаешь, что именно оно обходит. Worker — обычно небольшая квантованная локальная модель (Qwen3.8-27B в 2 битах, спекулятивное декодирование MTP, одна видеокарта на 12 ГБ, окно 64K–96K токенов, примерно 25–30 токенов в секунду), а Architect и Reviewer работают на Opus (около 130 токенов в секунду). Большинство решений следует из трёх фактов: **окно маленькое**, **генерация и prefill без кэша медленные** (полный prefill 78K токенов занимает около 4 минут, одно сжатие — около 7) и **дешёвый Worker полезен, только если кто-то проверяет его работу**. Для каждого пункта ниже указано, в чём обходное решение, зачем оно и чего стоит.

**1. Миссия и план сначала переводятся на английский, а отчёт возвращается на языке миссии.**
- *Что.* Architect пишет summary и каждую задачу на английском, на каком бы языке ни была миссия. Worker пишет заметки, отчёты и комментарии в коде на английском, Reviewer отвечает на английском. Тексты, которые должны появиться дословно (пути, идентификаторы, команды, строки интерфейса, заголовки, которых требует миссия), остаются на исходном языке и в кавычках. Для миссии не на английском Architect добавляет последнюю задачу `Mission report (<язык>)`, зависящую от всех остальных: Worker пишет отчёт по всей миссии на языке миссии, не меняя файлы, Reviewer проверяет язык, полноту и факты, а Telegram-уведомление `✅ WORKFLOW APPROVED` содержит этот отчёт.
- *Зачем.* Тот же текст на английском занимает на 5–15% меньше токенов (замер на одной задаче: Qwen 490 → 416, Claude 753 → 675), а при окне 64K и повторной отправке задачи в каждом ретрае это заметно. Небольшая локальная модель надёжнее следует правилам, когда задача, её собственные заметки и файлы на одном языке: с русской задачей и английскими системными правилами она смешивала оба. Итоговый отчёт на языке миссии нужен потому, что оператор читает результат на своём языке; отдельная последняя задача оставляет рабочим языком английский для всего, что модели читают во время прогона.
- *Цена.* Одна лишняя задача на миссию и шаг перевода, который может потерять смысл. Поэтому всё, что должно совпадать дословно, сохраняется и берётся в кавычки, а Reviewer сверяет отчёт с фактами. Промпты: `agent_prompts.example.csv` (блоки `LANGUAGE:`).

**2. Сжатие короткое, ранее и под реальное окно модели.**
- *Что.* Хук PreCompact просит у локальной модели сводку на 1500–2000 токенов по фиксированным разделам (Task, Findings, Changed files, Checks, Next step). Перед каждым локальным прогоном runner читает `--ctx-size` у llama-server и задаёт окно и порог автосжатия как процент от него; собственный `/compact` runner'а перед ретраем масштабируется так же. Вывод инструментов ограничен (`BASH_MAX_OUTPUT_LENGTH`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`), список инструментов сокращён, и системный промпт уменьшается с ~15K до ~4K токенов. Runner не запустится, если до конца окна остаётся меньше 20000 токенов запаса.
- *Зачем.* Стандартная сводка Claude Code около 8K токенов, на скорости локальной модели это около 6 минут; фиксированный порог 40000 перед возобновлением сжимал Worker'а на 96K уже на 46–52K, и сжатие шло дольше самого ретрая. Если окно изменено на сервере, но не в runner'е, модель падает с переполнением вместо сжатия.
- *Цена и нерешённое.* Каждое сжатие всё равно стоит минуты: кэш промпта llama-server не используется для запроса сжатия (полный повторный prefill префикса). Причина не найдена, см. «Известные открытые проблемы».

**3. Ретраи, лимит попыток и оператор.**
- *Что.* Ретрай после `needs_changes` продолжает сессию Worker'а (`--resume`) и передаёт ему его прошлый полный отчёт и ревью; Reviewer проверяет, что принятые ранее части не потеряны. Счётчик попыток берёт наибольшую попытку из журнала задачи, а задача останавливается на `max_attempts_per_task`, а не повторяется бесконечно. Переполнение контекста принудительно запускает сжатие перед ретраем; если сжатие не удалось, новая сессия получает дайджест старой (`GET /v1/sessions/{id}/digest`). `CLAUDE_RUNNER_RETRY_FRESH_SESSION=1` (по умолчанию выключено) начинает на ретрае после ревью новую сессию вместо сжатия старой; вход ретрая — задача, ревью, прошлый отчёт и дайджест в пределах бюджета символов.
- *Зачем.* Локальная модель иногда не может исправить замечание за три попытки, а Reviewer может находить новые замечания в каждой попытке. Бесконечный цикл сжигает часы времени GPU, поэтому лимит жёсткий.
- *Что делать с упавшей задачей.* Оператор читает последнее ревью, правит или принимает результат вручную и закрывает задачу: в журнале появляется строка `operator_accept` (не вердикт Reviewer'а), и миссия идёт дальше. Закрытие оператором остаётся видимым, поэтому в статистике оно не считается принятием ревью.
- *Вердикт Reviewer'а разбирается мягко.* Локальный Reviewer написал в `reason` неэкранированные двойные кавычки, и прогон упал; теперь `Parse Review` читает такой вердикт мягко, а если вердикта нет совсем, снова просит ту же сессию вернуть только JSON (до `reviewer_max_retries` раз).

**4. Агентов заставляет искать сначала хук, а не промпт.**
- *Что.* `ahawr-search "<вопрос>"` в образе claude-runner просит слой retrieval найти самые подходящие фрагменты в папке, где работает агент. При `SEARCH_FIRST` хук PreToolUse отклоняет разведочные `grep`/`rg`/`find`/`Grep`/`Glob`, пока сессия не вызвала `ahawr-search`, и после этого напоминает каждые несколько поисков. Команда fail-open (выход 0, если сервис недоступен), говорит, почему не сработала (запрос отклонён, ошибка сервиса, недоступность, таймаут), принимает привычные написания `--k`/`--budget` и ограничивает бюджет.
- *Зачем.* Правило в промпте («ОБЯЗАТЕЛЬНО: сначала ищи через ahawr-search») выполнялось в 4 из 26 прогонов локального Worker'а и только в начале, против 413 команд `grep`. Контекста retrieval в начале задачи тоже не хватает: точность по файлам у Worker'а была 46%, полнота 25%, а один патч на 3408 строк занимал 44,8% токенов контекста. Поэтому поиск по требованию, контекст Reviewer'а фокусируется на файлах, изменённых Worker'ом, а крупные `.patch` понижаются в выдаче.
- *Цена.* Поиск занимает несколько секунд, а первый поиск по новой папке индексирует её прямо в запросе (8–9 с на 30 небольших файлов, отсюда таймаут по умолчанию 30 с).

**5. Reviewer может читать, но не писать.**
- *Что.* Профиль Reviewer'а работает в режиме `dontAsk` с белым списком Bash только для чтения (`ahawr-search`, `grep`, `ls`, `wc`, `head`, `tail`, `cat`, `diff`, `bash -n`, `git` только для чтения) и запрещает перенаправление в файлы и `git … --output`. Список заменяется через `CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS`.
- *Зачем.* В `dontAsk` команда выполняется, только если её разрешает правило; одна локальная миссия записала 105 отказанных вызовов Bash у Reviewer'ов, и они судили отчёты, которые не могли проверить.
- *Размещение результатов.* Reviewer не видит `/tmp` Worker'а. Необязательное правило размещения результатов просит класть файлы, которые Reviewer должен проверить, в рабочую папку миссии и указывать в отчёте точный путь (`docs/ahawr-cheaper-retries.md` §5.3).

**6. То, что Reviewer не может проверить, принимает оператор.**
Reviewer судит отчёт Worker'а и файлы, которые может прочитать. Worker не может запустить Docker, собрать образ с GPU или поднять живой сервис, поэтому миссия, которой это нужно, может пройти ревью и не заработать (серия патчей однажды прошла все ревью, хотя не применялась, а пересобранный образ упал на первом запросе). Для таких миссий в критериях приёмки указано, что запускает оператор (например `docker build` и бенчмарк), и миссия не считается завершённой, пока это не пройдёт. Ручная правка рабочего дерева должна попадать и в артефакт (патч, файл под версионным контролем), а не только в дерево: ревью этой разницы не видит.

**7. Цифры стоимости — оценки.**
Claude Code сообщает стоимость каждого прогона. Для локальной модели образ оценивает её по ценам Claude Sonnet 5 (`claude-runner/managed-settings.json`), чтобы сравнивать локальный прогон с полностью Anthropic; это не счёт. Реальные траты — роли на Anthropic (обычно Reviewer и Architect на Opus). `cost_usd` прогона — его собственная доля: Claude Code восстанавливает итог сессии при `--resume`, поэтому runner вычитает итог предыдущего прогона.

**8. Telegram-уведомления короткие и безопасные.**
Уведомления о задаче и ревью содержат id задачи, попытку, балл и причину, обрезанную по предложению, а не всё ревью. Текст сначала обрезается, а потом экранируется для HTML: если экранировать до обрезки, можно разрезать сущность, и Telegram отклонит сообщение.

**9. Эксплуатационные детали, которые легко упустить.**
- Образ ставит `core.autocrlf=true`, поэтому Windows-репозиторий, смонтированный в контейнер, не показывает все CRLF-файлы изменёнными (85 ложных изменений однажды запутали проверку области правок).
- Прогон может идти до `CLAUDE_RUNNER_MAX_RUN_SECONDS` (в `.env` этого стека 10800); прогоны Worker'а с тяжёлой оценкой упирались в два часа.
- Живые выгрузки Data Tables и файлы миссий хранятся вне git (`data-tables/`); в репозитории лежат `hermes_config.example.csv`, `agent_prompts.example.csv` и `missions.example.csv`.
- Data Tables лежат в SQLite-файле n8n; править их напрямую (например, чтобы закрыть задачу или загрузить миссию) безопасно только при остановленном n8n и после копии базы. Где хватает интерфейса n8n, лучше пользоваться им.
- Worker и Reviewer не могут читать файлы `.env` (любые варианты, кроме `.env.example`) и делать `git commit` и `git push`; коммитит оператор.

**Известные открытые проблемы.**
- Кэш промпта llama-server не используется, когда Claude Code присылает запрос сжатия, поэтому каждое сжатие заново обрабатывает весь префикс (около 7 минут на 78K токенов; семь таких сжатий в одной задаче). Причина (сборка сервера, версия Claude Code или настройки кэша) не установлена.
- Метрика использования retrieval показывает 0 открытых файлов на живых данных: в сводках прогонов runner'а нет id миссии и задачи, по которым их можно связать с запросами retrieval.
- Встроенный хеширующий эмбеддер плохо ранжирует редкие идентификаторы.
- Сравнения качества, цены и скорости между локальной моделью и моделями Anthropic — единичные прогоны; измеренные числа см. в `CHANGELOG.md`.

### Компрессия Hermes-сессий

Для длинных Worker/Reviewer-сессий Run Manager использует отдельный TUI WebSocket путь Hermes:

```text
login → WS ticket → session.resume → session.compress → summary
```

RPC компрессии передаёт `session_id`, WS ticket, `keep_recent` и timeout. `model` и `provider` в этот RPC напрямую из n8n не передаются: модель компрессии выбирается Hermes из собственной конфигурации.

Для текущей конфигурации Hermes модель компрессии задаётся явно:

```yaml
auxiliary:
  compression:
    provider: openrouter
    model: openrouter/free
```

Это отдельная настройка и она не совпадает с `architect_model`, `worker_model` или `reviewer_model` из `hermes_config`.

Текущий backend Hermes, к которому подключается n8n для TUI-компрессии, запускается так:

```bash
hermes serve --host 0.0.0.0 --port 9119
```

После изменения `~/.hermes/config.yaml` необходимо полностью перезапустить именно этот процесс `hermes serve`, чтобы YAML был перечитан. Перезапуск n8n из-за изменения Hermes YAML не требуется. После запуска проверьте:

```text
HERMES_BACKEND_READY port=9119
```

В состоянии Run Manager хранятся отдельные `architect_session_id`, `worker_session_id` и `reviewer_session_id`. Компрессия должна работать с сессией выбранной текущим compression-target путём, а не автоматически брать сессию Architect.

Если в `Execute Command` виден wrapper вида:

```text
__HERMES_EXIT__=...
__HERMES_STDOUT__
{...}
__HERMES_STDERR__
```

JSON между `__HERMES_STDOUT__` и `__HERMES_STDERR__` является фактическим ответом Hermes. Ошибка `Model ... is not supported` означает проблему выбора model/provider для compression, а не проблему WebSocket или `session.resume`.

### Принципы проекта

**Workflow отвечает за orchestration.**  
Данные и prompts вынесены отдельно.

**Credentials отделены от Git.**  
Секреты должны находиться в n8n Credentials или другом secret storage.

**Длительные AI-запуски асинхронные.**  
Используются `run_id`, polling и retry.

**Каждая задача проверяется отдельно.**  
Reviewer принимает решение по текущей задаче.

**Состояние сохраняется.**  
Долгие запуски не зависят только от одной execution-сессии.

**Mission является data-driven.**  
Изменение миссии не требует переписывать оркестратор.

---

## License

Add the license that matches your intended distribution model.
