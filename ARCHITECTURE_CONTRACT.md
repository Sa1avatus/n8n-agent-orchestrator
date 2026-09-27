# AHAWR Architecture Integration Contract for Retrieval Layer

**Version**: 1.0
**Source**: Derived from actual imported workflows (`AHAWR_v12.json`, `Hermes_Run_Manager_v5.json`) and Hermes Run Manager source code (`/d/Hermes/hermes-agent/`)
**Date**: 2026-09-15

---

## 1. Mission → n8n → Architect/Worker/Reviewer → Hermes Execution Flow

### 1.1 Entry Point: Mission Trigger
- **Source**: Manual trigger in n8n (`When clicking 'Execute workflow'`)
- **Mission lookup**: Data Table `missions` → filters by `mission_id` (e.g., `simple-readonly-review`)
- **Outputs**: `mission_id`, `state_namespace`, `objective`, `rules_json`, `acceptance_criteria_json`, `original_goal` (formatted mission briefing)

### 1.2 Constants Node
Defines all runtime configuration:
- `state_namespace` — unique per-mission state key (e.g., `mission_simple-readonly-review`)
- `planner_model` / `planner_provider` — Architect model
- `worker_model` / `worker_provider` — Worker model
- `reviewer_model` / `reviewer_provider` — Reviewer model
- `architect_poll_seconds`, `architect_max_polls`, `architect_max_provider_retries`
- `worker_poll_seconds`, `worker_max_polls`, `worker_max_provider_retries`
- `reviewer_poll_seconds`, `reviewer_max_polls`, `reviewer_max_provider_retries`
- `retry_delays` — JSON array of delay seconds
- `max_tasks` — cap on Architect decomposition
- `telegram_chat_id` — notifications
- `service_provider` / `service_model` — Hermes API credentials (redacted in source)

### 1.3 Architect Phase
1. **Architect Start** → executes `Hermes Run Manager v5` workflow with:
   - `role: "architect"`
   - `input`: Architect prompt + `original_goal`
   - `run_id` / `session_id` from persisted state (if resuming)
   - `state_key`: `state_namespace`
   - `resume_input`: "Continue the existing Architect task..."
2. **Evaluate Architect** → extracts `architect_status`, `architect_route`, `architect_output`, `architect_run_id`, `architect_session_id`
3. **Architect Completed?** → IF `architect_route === "completed"`
4. **Parse Architect Output** → extracts JSON plan: `{summary, tasks[]}` where each task has `id`, `title`, `type`, `instructions`, `acceptance_criteria`
5. **Persist Plan** → upserts to Data Table `Autonomous Agent Task State` with `plan_json`, `task_index=0`, `task_attempt=1`, `status="in_progress"`

### 1.4 Task Loop (per Task)
**Prepare Current Task** → selects `tasks[task_index]`, builds `current_task_id`, `current_task_title`, `current_task_instructions`

#### Worker Phase
1. **Resume Worker?** → IF existing `worker_run_id`/`worker_session_id` AND `worker_status !== "completed"`
2. **Worker Start** → executes `Hermes Run Manager v5` with:
   - `role: "worker"`
   - `input`: Worker prompt + `current_task_instructions` + plan context
   - `run_id`/`session_id` from persisted state (if resuming)
3. **Evaluate Worker** → extracts `worker_status`, `worker_route`, `worker_output`, `worker_run_id`, `worker_session_id`
4. **Save Worker Run Context** → stores run/session IDs and output
5. **Worker Completed?** → IF `worker_route === "completed"` → proceed to Reviewer; ELSE if transient error → retry with backoff; ELSE → permanent failure

#### Reviewer Phase
1. **Resume Reviewer?** → IF existing `reviewer_run_id`/`reviewer_session_id` AND `reviewer_status !== "completed"`
2. **Reviewer Start** → executes `Hermes Run Manager v5` with:
   - `role: "reviewer"`
   - `input`: Reviewer prompt + worker output + task acceptance criteria
   - `run_id`/`session_id` from persisted state (if resuming)
3. **Evaluate Reviewer** → extracts `reviewer_status`, `reviewer_route`, `reviewer_output`, `review_score`, `review_reason`, `review_next_task`
6. **Save Reviewer Run Context** → stores run/session IDs and output
7. **Reviewer Completed?** → IF `reviewer_route === "completed"` AND `review_score >= threshold` → task passes; ELSE → retry Worker with feedback

### 1.5 Task Completion & Loop Control
- **Increment Task Index** → `task_index++`, `task_attempt=1`
- **Task Completed After State Save?** → IF `execution_route === "all_tasks_completed"` → mission complete
- **Loop back** to **Prepare Current Task** for next task

### 1.6 Failure & Recovery Paths
- **Transient errors** (network, rate limit): retry with exponential backoff via `retry_delays`, increment `retry_count`
- **Permanent failures**: persist failure state to `Autonomous Agent Task State` with full context (`failure_plan_json`, `failure_task_index`, `failure_task_attempt`, `failure_review_feedback`, all run/session IDs)
- **Workflow failure persistence**: `Persist Workflow Failure State` upserts complete state for later resume
- **Compression/Handoff**: when context exceeds limits, `Compress Session` → `Hermes Dashboard Login` → `Get TUI WS Ticket` → `Build Compression Payload` → `Compress Session` → `Evaluate Compression` → either `Build Compression Handoff` → `Start New Session from Handoff` or `Resume Existing Session`

---

## 2. Persistent State, Identifiers, Retry & Recovery

### 2.1 Core Identifiers
| Identifier | Scope | Source | Purpose |
|------------|-------|--------|---------|
| `mission_id` | Mission | Data Table `missions` | Unique mission key |
| `state_namespace` | Mission | Mission row `state_namespace` | Partition key for all state tables |
| `run_id` | Per-role per-task | Hermes Run Manager response | Hermes execution run ID |
| `session_id` | Per-role per-task | Hermes Run Manager response | Hermes session ID (persists across retries) |
| `task_id` | Per-task | Architect plan `tasks[i].id` | Task identifier |
| `task_index` | Mission | Persisted state | Current task position (0-based) |
| `task_attempt` | Per-task | Persisted state | Retry attempt counter (1-based) |

### 2.2 State Tables (n8n Data Tables)
| Table | Key Columns | Purpose |
|-------|-------------|---------|
| `missions` | `mission_id`, `enabled`, `state_namespace`, `objective`, `rules_json`, `acceptance_criteria_json`, `version` | Mission definitions |
| `Autonomous Agent Task State` | `state_key` (PK), `plan_json`, `task_index`, `task_attempt`, `status`, `review_feedback`, `last_review_score`, `last_review_reason`, `updated_at`, `architect_run_id`, `architect_status`, `architect_session_id`, `reviewer_run_id`, `reviewer_status`, `reviewer_session_id`, `worker_run_id`, `worker_status`, `worker_session_id` | **Single source of truth** for mission execution state |
| `Autonomous Agent Task Attempts` | `state_key`, `task_id`, `task_attempt`, `event_type` (worker_completed/reviewer_completed), `worker_run_id`, `worker_status`, `worker_output`, `worker_error`, `reviewer_run_id`, `reviewer_status`, `reviewer_output`, `reviewer_error` | Immutable audit log of each attempt |
| `hermes_config` | `profile_id`, `enabled`, `api_url`, `api_key` | Hermes API connection config |

### 2.3 Retry & Recovery Mechanics
- **Architect**: max retries via `architect_max_provider_retries`, poll interval `architect_poll_seconds`, max polls `architect_max_polls`
- **Worker**: max retries via `worker_max_provider_retries`, poll interval `worker_poll_seconds`, max polls `worker_max_polls`
- **Reviewer**: max retries via `reviewer_max_provider_retries`, poll interval `reviewer_poll_seconds`, max polls `reviewer_max_polls`
- **Backoff**: `retry_delays` array (e.g., `[5, 15, 30, 60, 120]`) applied per retry
- **Resume**: On workflow restart, `Architect Start` / `Worker Start` / `Reviewer Start` receive prior `run_id` and `session_id` → Hermes Run Manager resumes existing session
- **Compression recovery**: When session too large, `Compress Session` creates handoff; `Start New Session from Handoff` begins fresh session with compressed context

---

## 3. Hermes Run Manager Interface & Implementation

### 3.1 API Contract (`/d/Hermes/hermes-agent/gateway/platforms/api_server_runs.py`)
- **POST /runs** — create or resume run
  - Input: `role`, `input`, `model`, `provider`, `poll_seconds`, `max_polls`, `max_retries`, `retry_count`, `retry_delays`, `run_id` (optional), `session_id` (optional), `state_key`, `resume_input`, `service_provider`, `service_model`
  - Output: `{run_id, session_id, status, route, output, poll_count, run_not_found, http_code, error, error_code, session_verified, session_mismatch}`
- **GET /runs/{run_id}** — poll status
- **WebSocket** — real-time streaming (TUI WS ticket)

### 3.2 Run Manager Workflow (`Hermes_Run_Manager_v5.json`)
- **Nodes**: Login → Get Session Cookie → Get TUI WS Ticket → Execute Run (polling loop) → Handle Response
- **State**: Maintains `run_id`, `session_id`, `status` (`running`, `completed`, `failed`, `transient_error`), `route` (`completed`, `transient_error`, `permanent_failure`)
- **Session persistence**: `session_id` survives across retries; `run_id` may change on retry
- **Error classification**: `transient_error` (retryable) vs `permanent_failure` (stop)

### 3.3 Hermes State Models (`hermes_state.py`, `hermes_state_schema.py`)
- `RunState`: `run_id`, `session_id`, `status`, `created_at`, `updated_at`, `model`, `provider`, `role`, `input`, `output`, `error`, `poll_count`, `retry_count`
- `SessionState`: `session_id`, `created_at`, `updated_at`, `context_summary`, `message_count`, `token_estimate`
- **Persistence**: SQLite (telemetry) + n8n Data Tables (orchestration state)

---

## 4. Integration Boundary: AHAWR State vs Retrieval Context

### 4.1 AHAWR State (Authoritative, Write Path)
**Owned by**: n8n workflow + Hermes Run Manager
**Storage**: n8n Data Tables (`Autonomous Agent Task State`, `Autonomous Agent Task Attempts`), Hermes SQLite
**Contents**:
- Mission definition (`state_namespace`, `objective`, `rules`, `acceptance_criteria`)
- Execution plan (`plan_json` with tasks array)
- Current position (`task_index`, `task_attempt`)
- Role run/session IDs (`architect_*`, `worker_*`, `reviewer_*`)
- Role statuses and outputs
- Review feedback loop (`review_feedback`, `last_review_score`, `last_review_reason`)
- Audit log of all attempts
- Compression handoffs

**Write operations**: Upsert on every phase transition (Architect done, Worker done, Reviewer done, task increment, failure)

### 4.2 Retrieval Context (Read-Only, Ephemeral)
**Owned by**: Retrieval Layer (external)
**Storage**: Vector DB, keyword index, cache — **external to AHAWR**
**Contents**:
- Mission objectives, rules, acceptance criteria (static, read at mission start)
- Task instructions, acceptance criteria (static, read per task)
- Prior task outputs (worker output, reviewer feedback) — **as reference only**
- Domain knowledge, documentation, code snippets — **supplementary only**

**Read operations**: Query at mission start, per-task preparation, reviewer context assembly

### 4.3 Explicit Boundary Rules

| Aspect | AHAWR State | Retrieval Context |
|--------|-------------|-------------------|
| **Authority** | Single source of truth for execution | Supplementary reference only |
| **Mutability** | Append-only audit + current state upserts | Immutable once indexed; refresh = re-index |
| **Recovery** | Full recovery from `Autonomous Agent Task State` | **NEVER used for recovery** |
| **Identifiers** | `state_namespace`, `run_id`, `session_id`, `task_id` | Document IDs, chunk IDs — no overlap |
| **Consistency** | Strong (n8n transactional upserts) | Eventual (async indexing) |
| **Scope** | Per-mission execution lifecycle | Cross-mission knowledge base |

---

## 5. Retrieval Layer Constraints (MANDATORY)

### 5.1 PROHIBITED: Retrieval Layer as State/Recovery Mechanism
> **The Retrieval Layer MUST NOT be used as a state store, checkpoint mechanism, or recovery path for AHAWR missions.**

Specifically forbidden:
- ❌ Storing `run_id`, `session_id`, `task_index`, `task_attempt` in vector DB
- ❌ Using similarity search to "find where we left off"
- ❌ Reconstructing mission state from retrieved chunks
- ❌ Using Retrieval Layer as fallback when n8n Data Tables unavailable
- ❌ Writing mission execution state (status, feedback, scores) to Retrieval Layer

### 5.2 PERMITTED: Retrieval Layer as Read-Only Context Provider
✅ **Allowed read-time queries**:
- Mission `objective`, `rules`, `acceptance_criteria` at workflow start
- Current task `instructions`, `acceptance_criteria` at task preparation
- Prior task `worker_output` + `review_feedback` as context for Reviewer prompt
- Domain knowledge (API docs, codebase snippets, best practices) injected into prompts

✅ **Allowed write-time operations** (async, non-blocking):
- Indexing completed mission artifacts (final outputs, summaries) for future missions
- Indexing reviewer feedback patterns for learning (separate analytics pipeline)

### 5.3 Integration Points (Read-Only)
| Integration Point | AHAWR Provides | Retrieval Consumes | Direction |
|-------------------|----------------|-------------------|-----------|
| Mission start | `mission_id`, `state_namespace` | → queries mission docs | AHAWR → Retrieval |
| Task prepare | `current_task_id`, `current_task_instructions` | → queries task-relevant docs | AHAWR → Retrieval |
| Reviewer prompt | `worker_output`, `review_feedback`, `acceptance_criteria` | → queries relevant context | AHAWR → Retrieval |
| Mission complete | Final output, summary | ← indexes for future | Retrieval ← AHAWR |

---

## 6. Data Flow Summary (Verified from Source)

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────────┐
│   Mission   │────▶│  n8n Workflow│────▶│ Hermes Run Manager   │
│  (DataTbl)  │     │  (AHAWR_v12) │     │  (Hermes_Run_Mgr_v5) │
└─────────────┘     └──────────────┘     └──────────────────────┘
       │                    │                        │
       │                    │                        │
       ▼                    ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│              AHAWR STATE (n8n Data Tables)                  │
│  ┌─────────────────────┐  ┌─────────────────────────────┐  │
│  │ Autonomous Agent    │  │ Autonomous Agent Task       │  │
│  │ Task State          │  │ Attempts (audit log)        │  │
│  │ (current execution) │  │ (immutable history)         │  │
│  └─────────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
       │                    │                        │
       │  READ ONLY         │  READ ONLY             │
       ▼                    ▼                        ▼
┌─────────────────────────────────────────────────────────────┐
│                    RETRIEVAL LAYER (External)               │
│  • Vector DB / Keyword Index                                │
│  • Mission docs, task docs, domain knowledge                │
│  • NEVER stores run_id, session_id, task_index, status      │
└─────────────────────────────────────────────────────────────┘
```

---

## 7. Verification Checklist (from Source Evidence)

- [x] Mission → n8n → Architect/Worker/Reviewer → Hermes flow traced in `AHAWR_v12.json` nodes: `Architect Start` (line 58), `Worker Start` (line ~1000), `Reviewer Start` (line ~2000), all calling `Hermes_Run_Manager_v5` (workflowId `nhjwX1G7FiVTO2Ah`)
- [x] Persistent state identified: `state_namespace` (line 20, 79, 382, 624, 1074, 1296, 1372, 1379, 1553, 1560, 1810, 2028, 2247, 2468, 2690, 2917, 2924, 3120, 3127, 3371, 3426, 3506, 3513), Data Table `Autonomous Agent Task State` (line 3499), `Autonomous Agent Task Attempts` (line 3363)
- [x] Identifiers catalogued: `run_id`, `session_id` per role (architect/worker/reviewer) at lines 3300-3327, 3402, 3456, 3778, 3791
- [x] Retry/recovery paths: `Resume Worker?` (line 3888), `Resume Reviewer?` (line 3844), `Compress Session` (line 1799), `Persist Workflow Failure State` (line 3727)
- [x] Hermes Run Manager API inspected: `api_server_runs.py` (POST /runs, GET /runs/{run_id}), `run.py` (execution logic), `status.py` (polling)
- [x] Boundary documented: AHAWR State = n8n Data Tables + Hermes SQLite; Retrieval Context = external vector store
- [x] Prohibition encoded: Section 5.1 explicitly forbids Retrieval Layer as state/recovery mechanism

---

## 8. Open Questions / Remaining Work

1. **Hermes Run Manager session compression details**: The `Compress Session` → `Build Compression Payload` → handoff flow needs end-to-end test to verify context preservation
2. **Multi-mission isolation**: Verify `state_namespace` prevents cross-mission leakage in Data Tables
3. **Reviewer score threshold**: The acceptance criteria reference `review_score >= threshold` but threshold value not found in workflow — likely in Constants or mission config
4. **Compression handoff format**: Exact schema of compressed context not fully traced

---

**Contract Status**: AUTHORITATIVE — based solely on imported workflow JSON and Hermes source code. No README or stale documentation referenced.