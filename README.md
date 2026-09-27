# n8n + Hermes Autonomous Agent Orchestrator

**English** | [Русская версия](#русская-версия)

An n8n-based orchestration system for running autonomous multi-agent development workflows through a Hermes Gateway.

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

The main workflow is the **Autonomous Hermes Architect Worker Reviewer (AHAWR)** workflow.

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

- `hermes_config.csv`;
- `agent_prompts.csv`;
- `missions.csv`;
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

The repository is expected to contain files similar to:

```text
.
├── AHAWR_v11.json
├── Hermes_Run_Manager_v4.json
├── hermes_config.csv
├── agent_prompts.csv
├── missions.csv
├── docker-compose.yml
├── .gitignore
└── README.md
```

The exact list may change as the project evolves.

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
git clone https://github.com/Sa1avatus/n8n-hermes-agents.git
cd n8n-hermes-agents
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

#### 7. Import the current `Hermes_Run_Manager_v4.json`

In n8n:

1. Import the current `Hermes_Run_Manager_v4.json`.
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

Import the corresponding CSV files.

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

#### 10. Import `AHAWR_v11.json`

Import:

```text
AHAWR_v11.json
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

- Hybrid retrieval: BM25 + vectors + symbol search, reciprocal-rank fusion, text-only cross-encoder reranking through `reranker-service`, deterministic filters and ranking.
- Separate Worker and Reviewer retrieval profiles.
- Every chunk carries provenance: source type, path, symbol/section, chunk id, content hash, snapshot/version, scores and rank.
- Incremental indexing with chunk-level invalidation, and a retrieval cache with semantic query fingerprints for retries.
- Its own local index by default; `rag-platform` can be attached as an external backend, with automatic fallback to the local index.
- An Eval Harness (gold/silver datasets, retrieval, system and AHAWR-level metrics) decides on every later extension.

`AHAWR_v13.json` calls `POST http://ahawr-retrieval:8500/retrieve` before `Worker Start` and `Reviewer Start`. The call is fail-open, and retrieval is off unless `hermes_config.retrieval_enabled` is `true`. With retrieval off, v13 behaves like v12. Set `AHAWR_WORKSPACE_DIR` and the optional `RETRIEVAL_*` values in `.env` (see `.env.example`); the workspace is mounted read-only into the retrieval container at `/workspace`. See [`docs/retrieval/`](docs/retrieval/ARCHITECTURE.md), [`retrieval-service/README.md`](retrieval-service/README.md) and [`docs/retrieval/INTEGRATION.md`](docs/retrieval/INTEGRATION.md).

### AHAWR on Claude Code (`claude-runner`)

`AHAWR_v13_ClaudeCode.json` is AHAWR v13 with **Claude Code instead of Hermes** as the execution engine. Claude Code has no HTTP run API of its own. `claude-runner/` runs the Claude Code CLI headless behind the same `/v1/runs` contract as the Hermes gateway, so orchestration, Data Table state, retries and recovery stay the same.

- Container `claude-runner` in the same compose stack: `docker compose up -d --build` builds and starts it together with n8n, `ahawr-retrieval` and `litellm`. n8n reaches it at `http://claude-runner:8700`; no host port is published.
- `Claude_Code_Run_Manager_v1.json` is `Hermes_Run_Manager_v5.json` with the runner endpoints and a role field. The Hermes TUI/WebSocket compression is replaced by `POST /v1/sessions/{id}/compact`, which runs Claude Code `/compact` only above a context threshold.
- Sessions are resumable Claude Code sessions (`--resume`), kept in a volume. A run interrupted by a runner restart is reported as `run_not_found`, so the Run Manager continues the saved session.
- Per-role permissions:
  - Worker: `bypassPermissions`, but it cannot read `.env` and cannot `git commit` or `git push`;
  - Architect and Reviewer: read-only.
- Model access: `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), or Bedrock/Vertex/Foundry.
- Projects: `missions.working_directory` (e.g. `D:\OpenAIProjects\app`) is where Claude Code runs and what retrieval indexes for that mission. The drive is mounted into the containers (`AHAWR_HOST_DRIVE`, default `D:\`).
- Providers per role, as with Hermes: roles with `*_provider=anthropic` go to Anthropic, and a role with `*_provider=local` goes through the `litellm` container to a local OpenAI-compatible llama.cpp server. See `litellm/config.yaml` and the `CLAUDE_RUNNER_PROVIDER_LOCAL__*` block of `.env.example`.
- Configuration: the `claude-code` row and the `runner_url` column of `hermes_config`. Models are `opus` / `sonnet` / `haiku` or full model names.

The Hermes workflows are unchanged, and both variants can be imported side by side. See [`claude-runner/README.md`](claude-runner/README.md).

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

Это система оркестрации автономных AI-агентов на базе **n8n + Hermes Gateway**.

Главный workflow — **Autonomous Hermes Architect Worker Reviewer (AHAWR)**.

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
hermes_config.csv
agent_prompts.csv
missions.csv
AHAWR_v11.json
Hermes_Run_Manager_v4.json
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

Типичная структура:

```text
.
├── AHAWR_v11.json
├── Hermes_Run_Manager_v4.json
├── hermes_config.csv
├── agent_prompts.csv
├── missions.csv
├── docker-compose.yml
├── .gitignore
└── README.md
```

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
git clone https://github.com/Sa1avatus/n8n-hermes-agents.git
cd n8n-hermes-agents
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

#### 7. Импортировать `Hermes_Run_Manager_v4.json`

В n8n:

1. Импортируйте `Hermes_Run_Manager_v4.json`.
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

Импортируйте соответствующие CSV.

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

#### 10. Импортировать `AHAWR_v11.json`

Импортируйте:

```text
AHAWR_v11.json
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

- Hybrid retrieval: BM25 + векторы + поиск по символам, RRF, text-only cross-encoder через `reranker-service`, детерминированные фильтры и ранжирование.
- Отдельные профили Worker и Reviewer.
- У каждого чанка есть provenance: тип источника, путь, символ/секция, chunk id, content hash, snapshot/version, scores и rank.
- Инкрементальная индексация, инвалидация на уровне чанков, кэш с семантическим fingerprint для повторных попыток.
- По умолчанию используется собственный локальный индекс. `rag-platform` можно подключить как внешний бэкенд; если он недоступен, сервис автоматически переходит на локальный индекс.
- Все дальнейшие расширения принимаются по данным Eval Harness (gold/silver наборы; метрики retrieval, системы и AHAWR).

`AHAWR_v13.json` вызывает `POST http://ahawr-retrieval:8500/retrieve` перед `Worker Start` и `Reviewer Start`. Ошибка или таймаут не останавливают задачу. Путь к workspace и параметры `RETRIEVAL_*` задаются в `.env` (см. `.env.example`); workspace монтируется в контейнер retrieval только на чтение в `/workspace`. Retrieval выключен, пока в `hermes_config` не задано `retrieval_enabled = true`; в этом режиме v13 работает как v12. Подробности: [`docs/retrieval/`](docs/retrieval/ARCHITECTURE.md), [`docs/retrieval/INTEGRATION.md`](docs/retrieval/INTEGRATION.md).


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

Воркфлоу для Hermes не изменены, обе версии можно импортировать одновременно. Подробности в [`claude-runner/README.md`](claude-runner/README.md).
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
