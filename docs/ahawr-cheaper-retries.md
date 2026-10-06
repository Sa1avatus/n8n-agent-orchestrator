# Cheaper retries: fresh-session retry, microcompact, context budget, mission criteria, Reviewer access

Design and estimates for the `ahawr-retry-and-microcompact` mission (T006). All numbers
are from T002 measurements (`docs/ahawr-cheaper-retries-notes.md` §4) and T003–T005
findings, with sources cited per figure.

## 1. Retry with /compact vs fresh session: time and risk

### 1.1 Time comparison

**Compaction (the cost avoided by fresh-session retry):**

The pre-retry runner `/compact` (`POST /v1/sessions/{id}/compact`, `runs.py:448`)
generates a structured summary via the local model. Measured durations from runner
events (notes §4.1, §4.2):

| Run id | pre→post tokens | Duration | Source |
|---|---|---|---|
| cmp_9c9e19e8 | 43269→11866 | 8m31s (510,117 ms) | notes:198 |
| cmp_fa43f633 | 43841→8074 | 6m54s (413,127 ms) | notes:199 |
| cmp_0218a78c | 43527→9558 | 6m51s (409,577 ms) | notes:215 |
| cmp_7f3d625c | 45068→7776 | 9m34s (572,898 ms) | notes:216 |
| cmp_734625e9 | 61766→2952 | 6m47s (405,717 ms) | notes:200 |

Total for the four fast-compaction-era compactions: **31m50s ≈ 32 min**
(notes:234–239, reproducing the mission text's "32 min, 5–8 min each").

Each compaction = prefill of the full transcript (cache-friendly, ~6 s from
checkpoint per notes:296) + generation of the summary (the dominant cost).
With the short structured instructions (≤2000 tokens, `CLAUDE_RUNNER_COMPACT_INSTRUCTIONS`,
delivered via the PreCompact hook, `claude_cli.py:41-60`),
summaries are 1500–2000 tokens; before that they were ~8K and took ~350 s
(notes:296). The 6m07s–9m34s range is dominated by summary generation at
~23–32 tok/s (variant F, mission text, not verified, notes:334).

**Fresh-session retry prefill:**

The fresh retry input (task text + review feedback + previous report + digest)
is ~10–15K tokens. At the prefill rate of 194–276 tok/s stated in the mission
text (notes §4.6 row 4 — mission text, not verified; source is llama-cpp-server
logs that are not in the repo):

$$t_{\text{prefill}} = \frac{10\text{–}15\text{K tokens}}{194\text{–}276 \text{ tok/s}} = 36\text{–}77 \text{ s}$$

Using the rounded ~250 tok/s midpoint (194–276 / 2):

$$t_{\text{prefill}} = \frac{10\text{–}15\text{K}}{250} = 40\text{–}60 \text{ s}$$

**Comparison:**

| Approach | Time | Source |
|---|---|---|
| Compaction + resume | 6m07s–9m34s (366–573 s) | notes:270 (pre-retry range), notes:198–216, 224, 255 |
| Fresh-session prefill | 36–77 s (40–60 s at 250 tok/s) | notes §4.6 row 4 + formula |

**Savings per retry:** the pre-retry compaction is 366–573 s (6m07s–9m34s,
notes:270); the fresh prefill is 40–60 s, so the saved time is
(366 − 60) / 60 ≈ 5.1 to (573 − 40) / 60 ≈ 8.9 min per retry. Over the fast-compaction-era retries, this equals the 4 measured
pre-retry compactions, **31m50s** (notes:234–239), minus 4 × (40–60 s) of
fresh prefill:

$$31\text{m50s} - 4 \times (40\text{–}60 \text{ s}) = 28\text{–}29 \text{ min per mission}$$

### 1.2 Context-loss risks of fresh-session retry

The fresh session starts with only the retry input. Context lost vs. resume:

| Risk | What is lost | Mitigation in retry input |
|---|---|---|
| Prior reads and findings | File contents, line references, intermediate grep/read results | Digest carries `file:line` findings and changed files (handoff.py, `TEXT_CHARS=1500`, `RESULT_CHARS=3000` per notes:124); previous report (truncated 16000 chars) in retry input (Build Worker Run Input, l3964) |
| Tool results (Bash output, grep results) | Large tool outputs that filled the context | Digest caps tool input/result at 300/400 chars (notes:124); the Worker re-runs only what the review questions — it does not need the original output verbatim |
| Mid-task state (partial changes, open questions) | Unfinished edits, hypotheses, "next step" | Digest includes a "Next step" section (short compact instructions, CHANGELOG 2026-09-29); the previous report names changed files |
| Mission-level context (other tasks, mission goal) | Full mission history | Retry input includes the mission acceptance criteria block (item 4, §4 below) and the task's acceptance criteria; the mission goal is in `original_goal` (notes:59–62) |
| Reviewer's prior assessment | Earlier review scores and reasons | `review_feedback` is in the retry input (Build Worker Run Input, l3964); the previous review block in the Reviewer input (l3978) |
| Prompt cache | The llama-server prompt cache built during the old session | Fresh prefill rebuilds the cache from scratch; the first step after compaction also re-prefills 15,502 tokens (4,263 cached, notes:304), so this cost is comparable either way |

**Key mitigation:** the digest (`GET /v1/sessions/{id}/digest`, `api.py:185`,
`session_digest` at `handoff.py:75`) is bounded by `max_chars` — requested at
16,000 in the workflow (Get Session Digest, l1275), API default 12,000, range
500–200,000 (notes:128, 163) — and carries the most recent 500 runs' findings
in newest-first order. The previous report (16,000-char truncation) and the
review feedback cover the rest. Together they reconstruct enough context for
the Worker to act on the review without re-reading files it already read.

### 1.3 Recommendation

**Default:** keep the current behaviour (compact-then-resume). It is the
conservative choice: the compaction is cache-compatible (notes:296) and the
resume reuses the session's transcript.

**Switch:** `CLAUDE_RUNNER_RETRY_FRESH_SESSION` (proposed, not yet in code;
notes:174, 353). When set, `_bind` (`runs.py:181`) starts a fresh
`claude_session_id` instead of resuming, and the runner fetches
`session_digest(old_session_id)` and prepends it to the input. This is
per-provider (`ProviderProfile.retry_fresh_session`), parsed alongside
`COMPACT_MIN_PCT` / `SEARCH_FIRST` in `_providers` (`config.py:279`).

**Workflow change (applied in T009, §5):** `Build Worker Run Input`
(AHAWR_v13_ClaudeCode.json:3964) computes `freshRetry`, `retryDigest`,
`retryInput` at top level (flag + non-empty session + non-empty
`review_feedback`); when on, the returned json uses
`input: freshRetry ? retryInput : (unfinished ? resumeText : fullInput)` and
`session_id: freshRetry ? '' : ...` and carries `retry_fresh_session`,
`retry_input_chars`, `previous_session_digest`. `Start / Resume Run`
(Claude_Code_Run_Manager_v1.json:71) omits `session_id` when the flag is on.
Reuse `Build Compression Handoff` (l1155) and `Start New Session from Handoff`
(l1198) — already built for the digest path.

## 2. Tool-result clearing (microcompact) findings

### 2.1 Mechanism (verified from the Claude Code 2.1.284 binary, notes §4.8)

- **Mode resolver** `fr()` (offset 200278090): reads GrowthBook feature
  `tengu_zany_pike` (offset 200271979). Default mode `"off"`;
  `"on"` clears tool uses when no replies remain; `"shadow"` never clears.
- **Planner** `bNo()` (offset 200272569): plans a
  `clear_tool_uses_20250919` edit only in `"on"` mode.
- **Beta gate** (offset 199443338):
  ```js
  n = a.USE_API_CONTEXT_MANAGEMENT && !1   // forced to false
  return e.firstPartyCapabilityBetas && (n || r)
  ```
  The `&& !1` neutralises `USE_API_CONTEXT_MANAGEMENT` regardless of its value.
  The beta header `context-management-2025-06-27` is added only when
  `firstPartyCapabilityBetas` is true.
- **Provider detection** (offset 197608118, `Uh`): returns true only for
  `api.anthropic.com`. For a custom `ANTHROPIC_BASE_URL` (LiteLLM at
  `127.0.0.1:8033`), `firstPartyCapabilityBetas` is false (notes:437, 441).
- **Request body** (offset 206189172): `context_management` is included only
  when the beta is active and the edit is planned.

### 2.2 Applicability for provider local

**Not applicable** (notes:628–636). The chain:

1. `Uh()` → false for LiteLLM host → `Us()` → false → `firstPartyCapabilityBetas` → false.
2. Beta gate → false → no `context-management-2025-06-27` header.
3. Without the beta, `Mk && jm && lr.includes(_be)` → false → no
   `context_management` edit in the request.
4. GrowthBook is unreachable/disabled for the local runner, so `fr()` → `"off"`
   (hypothesis, notes:633) — but the verdict rests on the beta gate, not the mode.

Binary evidence (notes §4.8; the quoted gates are from the installed Claude Code
binary, byte offsets as given):

- `USE_API_CONTEXT_MANAGEMENT` has exactly one code usage, offset `199443338`:
  ```js
  let n = a.USE_API_CONTEXT_MANAGEMENT && !1,   // forced to false
  ...
  return e.firstPartyCapabilityBetas && (n || r);
  ```
  The `&& !1` neutralises the env var: setting `USE_API_CONTEXT_MANAGEMENT=1`
  can never add the `context-management-2025-06-27` beta. The only fallback is
  `r = rN(model)` — the model-capability check — which likewise requires
  `firstPartyCapabilityBetas`.
- Provider check `Uh`, offset `197608118`:
  ```js
  function Uh(e){
    try{ let t=new URL(e).host;
      return ["api.anthropic.com"].includes(t);
    }catch{return !1}
  }
  ```
  For a custom `ANTHROPIC_BASE_URL` (LiteLLM at `127.0.0.1:8033`) this is
  false, so `firstPartyCapabilityBetas` is false and the gate above is false.
- Request body, offset `206189172`:
  ```js
  ...Mk && jm && lr.includes(_be) && {context_management: Mk}
  ```
  the `context_management` edit reaches the request only when the beta is
  active, which it is not for the local provider.

**T008 decision (no runner switch):** even with a runner switch, the env vars
(`USE_API_CONTEXT_MANAGEMENT`, `_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL=1`,
`CLAUDE_CODE_MODEL_CAPABILITIES`) that a provider env block could pass to the
CLI cannot enable the beta — the installed CLI's gate forces `n` to `false`
and `firstPartyCapabilityBetas` to `false` for any non-`api.anthropic.com`
host. The switch would be a dead end, so no switch is added. The runner's
working levers for keeping tool results small are
`BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`
(CHANGELOG 2026-09-29), and the short PreCompact instructions.

The LiteLLM/llama.cpp server would not understand the `context_management`
field even if the beta were forced (`_CLAUDE_CODE_ASSUME_FIRST_PARTY_BASE_URL=1`
plus `CLAUDE_CODE_MODEL_CAPABILITIES` with `"context_management"`), so the edit
would be ignored server-side. This is an unverified hypothesis (notes:636).

**Conclusion:** microcompact / tool-result clearing cannot be enabled for the
local provider with the current CLI. The runner's existing levers are
`BASH_MAX_OUTPUT_LENGTH=12000`, `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS=10000`
(CHANGELOG 2026-09-29), and the short PreCompact instructions.

## 3. Smaller context: generation speed vs compaction frequency

### 3.1 The trade-off

A smaller context fills up faster, so it needs more compactions; a larger
context compacts less often. The two candidate sizes from the measurements:
~15K (the fresh-retry input size, §6) and ~44K (the pre-compact size the
fast-compaction mission ran at, notes:230).

The only measured generation rates in scope are from the `ahawr-rag-effectiveness`
run on variant F (retry mission text line 7, notes:334 — mission text, not
verified): **23–32 tok/s** (MTP acceptance 64–87%), with prefill **194–276
tok/s**. The prefill figure is cache-dependent (notes:296), so it is not a
clean per-context-speed measurement; the generation range (23–32) is what the
comparison below should use.

| Context size | Generation speed | Source |
|---|---|---|
| ~15K (fresh-retry input) | 23–32 tok/s (variant F, no per-context split) | retry mission text line 7, notes:334 |
| ~44K (fast-compaction pre-compact) | 23–32 tok/s (variant F, no per-context split) | retry mission text line 7, notes:334 |

Because no per-context speed split is measured, the "smaller context is
faster" premise is **not established** — the 23–32 tok/s range covers both.
The trade-off therefore reduces to compaction frequency alone.

### 3.2 Compaction frequency

At the 67.66% trigger, auto-compact fires every ~10–20 steps
(notes:311–313, mission text; not verified from events). On the probed
**98,304-token window** (notes:204, `cmp_734625e9` seq 2 `context_window`
probe), the 67.66% trigger is **≈66,500 tokens** (98,304 × 0.6766 ≈ 66,513);
the static `44,344/65,536` figure (`compact_pct`, context_probe.py:90) is
the same share applied to the 65,536 static window. A 15K context would
hit the trigger sooner — roughly every 5–10 steps if each step adds
~5–8K tokens (typical Bash/Read outputs of 10–18K chars, notes:297).

### 3.3 Net effect

Since no per-context generation-speed split is measured (§3.1), there is no
quantified generation saving from shrinking the context. The only measurable
cost is compaction frequency:

- At the 67.66% trigger (≈66,500 tokens of the probed 98,304-token window,
  notes:204; `compact_pct` is 44,344/65,536 of the 65,536 static window,
  context_probe.py:90), auto-compact fires every ~10–20 steps (notes:311–313,
  mission text; not verified from events). A 15K context hits the trigger
  roughly twice as often (every ~5–10 steps if each step adds ~5–8K tokens).
- Each compaction is 6m07s–9m34s (measured, notes:270; the 4 fast-compaction
  compactions 6m51s–9m34s per notes:198–199 and 215–216; the 6m47s one is
  cmp_734625e9 of the rag-effectiveness mission).
- Doubling compaction frequency from ~4 to ~8 per task is a **hypothesis**
  (48 compactions / 12 tasks, mission text, not verified, notes:331 row 1).
  It would add ~4 compactions ≈ **20–35 min per task**.

**Conclusion:** shrinking the context to ~15K is **not worth it**. Even if it
were somewhat faster per token (unmeasured), the extra ~20 min of compaction
per task dominates. The better levers are the short PreCompact instructions
(already deployed) and `BASH_MAX_OUTPUT_LENGTH`, which cut the per-step context
growth and thus reduce compaction frequency without changing the window. The
compaction itself (6m07s–9m34s) is already dominated by summary generation,
not prefill, so further shortening the instructions has little to gain.

## 4. Mission acceptance criteria block: size and when to include

### 4.1 Current state

The per-task `acceptance_criteria` is injected into the Worker and Reviewer
prompts (Build Worker Run Input l3964, Build Reviewer Run Input l3978).
The **mission-level** `acceptance_criteria` is only embedded in
`original_goal` (notes:59–62, 73–76) and is **not** read directly by either
Build Run Input node. The Worker sees only the task's criteria; the Reviewer
sees the mission goal (which includes the mission criteria as part of
`original_goal`).

### 4.2 Size estimate

Measured mission-level `acceptance_criteria_json` sizes (mission CSVs,
`data-tables/missions/mission_*.csv` line 9):

| Mission | # criteria | Chars (JSON) | ~Tokens (÷4) | Prefill at 250 tok/s |
|---|---|---|---|---|
| ahawr-retry-and-microcompact | 8 | 1,366 | ~342 | 1.37 s |
| ahawr-rag-effectiveness | 11 | 2,074 | ~519 | 2.07 s |
| ahawr-fast-compaction | 8 | 1,528 | ~382 | 1.53 s |

Adding the mission-level criteria to the Worker input (plus a one-line
objective summary, ~50 tokens) adds **~340–520 tokens, ~1.4–2.1 s of prefill**.
This is negligible against the 36–77 s prefill of the full retry input.

### 4.3 When to include: always vs flagged tasks

**Always:** the cost is ~1–2 s of prefill per run. The benefit: the Worker can
verify mission-level criteria directly (e.g., "mission report: table of
criterion → evidence" in T019 of ahawr-rag-effectiveness) without
reconstructing the list from memory. For tasks that depend on the mission
criteria (mission-report tasks, final verification tasks), this is the
difference between passing and failing.

**Flagged tasks only:** if only the last task or tasks with a specific flag
(e.g., "depends on mission criteria") receive the block, the cost is zero for
most runs. But the Worker cannot know which tasks are flagged without
reading the plan, and the flag would need to be in the task object
(not currently there).

**Recommendation:** include the mission-level criteria **always when the
switch is enabled** (and the Reviewer already gets it via `original_goal`).
The ~1–2 s prefill cost is trivial; the risk of a Worker failing a
mission-criteria-dependent task is the larger concern, which is why enabling
it (rather than the default `false`) is recommended.

### 4.4 Workflow change

- **Build Worker Run Input** (AHAWR_v13_ClaudeCode.json:3964): add
  `$('Mission').first().json.acceptance_criteria` and a one-line objective
  summary to the `fullInput` block.
- **Build Reviewer Run Input** (AHAWR_v13_ClaudeCode.json:3978): **no change
  needed** — the mission criteria are already in `original_goal` (notes:76).

**Switch:** the field `worker_mission_criteria` (bool, default `false`) and
`mission_criteria_chars` (number, default `1600`) are read from the flowing
state/item by the two Build Run Input nodes. No existing node currently sets
them — see §6.6 for the exact edit to enable the block.

## 5. Reviewer access to task results: item 5 decision

### 5.1 The problem

The Architect plans research tasks with notes in `/tmp` of the agent (e.g.,
`/tmp/ahawr-theme/T001-notes.md`). The Reviewer runs in `dontAsk` mode
(`config.py:326`) with read access only to the mission's working directory.
Result: every such task loses an attempt to "cannot check the file"
(T001 and T002 of ahawr-dashboard-theme, notes:638).

### 5.2 Options

**Option A — `CLAUDE_RUNNER_REVIEWER_ADD_DIRS`:** add the Worker's scratch
folder to the Reviewer's `--add-dir` flags. Security: the Reviewer stays
read-only (`dontAsk` + deny list blocks `Edit`/`Write`/`NotebookEdit`/
`Bash(* >*)`). Risk: exposes the Worker's scratch (possible secrets,
unrelated data). Requires the Worker to place results in a documented path.

**Option B — prompt rule:** add a rule to the Architect and Worker prompts:
"Place any file the Reviewer must verify in the mission working directory
(or a subfolder), and cite its path in the report." The Reviewer checks the
named path with Grep/Read. No new directory access needed. The Reviewer's
read surface stays minimal (mission folder only).

**Chosen: Option B** (notes:668–674). Justification:
1. Security: minimal read surface, no `/tmp` exposure.
2. Explicitness: the placement contract is part of the task plan (Architect
   names the path in `scope`/`acceptance_criteria`).
3. Reviewer stays read-only without new permissions.

### 5.3 Switch (opt-in prompt rule, off by default)

The shipped defaults of `agent_prompts.example.csv` do **not** include the
result placement rule. The rule is opt-in: an operator who wants it
appends the following paragraph to the `system_prompt` column of the
`architect` and `worker` rows in the mission's `agent_prompts` data table
(the CSV row ends with `",Architect planner prompt"` / `",Worker execution prompt"` —
insert the paragraph as a new paragraph before the closing quote).

**Exact opt-in paragraphs**

Architect row (append after the `Every task MUST contain:` list, before `LANGUAGE:`):

```
RESULT PLACEMENT RULE: name in each task's `scope` or `acceptance_criteria` the exact path(s) — inside the mission working directory or a subfolder of it — where the Worker must place the files the Reviewer will verify (notes, artifacts, logs). Do not place those files in /tmp or outside the mission folder: the Reviewer reads only the mission working directory.
```

Worker row (append after the `FIRST ACTION` block, before `Do not commit or push changes.`):

```
RESULT PLACEMENT RULE: place every file the Reviewer must verify (notes, artifacts, logs) in the mission working directory or a subfolder of it — never in /tmp or elsewhere — and cite its exact path in your report. The Reviewer reads only the mission working directory.
```

**Enable step**

Edit the `agent_prompts` data table in the mission (or the
`agent_prompts.example.csv` file if that is the source of the table):

1. Open the `architect` row and insert the Architect paragraph above
   (between the `Every task MUST contain:` list and the `LANGUAGE:` block).
2. Open the `worker` row and insert the Worker paragraph above
   (between the `FIRST ACTION` block and the `Do not commit or push changes.` line).
3. Re-import/reload the table. No workflow node, per-run flag, or runner
   restart is needed — the rule lives entirely in the prompt text.

Without these edits the rule is **off**: the Reviewer still reads only the
mission working directory and cannot reach /tmp.

Comparison with `CLAUDE_RUNNER_REVIEWER_ADD_DIRS` (.env.example:118,
claude-runner/README.md:83): that switch is static per runner, requires a
restart, and widens read access. The prompt rule changes only the
contract and adds no directory access.

**Example research task that would now pass on the first attempt (based on
T001 of `ahawr-dashboard-theme`):**

The original T001 of `ahawr-dashboard-theme` was a research task: investigate
the dashboard's theme implementation and put the findings in notes. The
Architect wrote the notes path as `/tmp/ahawr-theme/T001-notes.md`
(`docs/ahawr-cheaper-retries-notes.md:638`). The Reviewer runs in `dontAsk`
mode with read access only to the mission working directory (`config.py:326`,
`Build Reviewer Run Input` at `AHAWR_v13_ClaudeCode.json:3978`), so it could
not open the /tmp file and the attempt was lost to "cannot check the file".

With the result placement rule the same task now plans and verifies cleanly:

- **Architect** (rule: `agent_prompts.example.csv`, Architect row): names the
  notes path inside the mission folder in the task's `scope`/
  `acceptance_criteria`, e.g. `T001-notes.md` in the mission working directory
  `D:\n8n` (or a subfolder such as `D:\n8n\ahawr-theme-notes\T001-notes.md`).
- **Worker** (rule: `agent_prompts.example.csv`, Worker row): writes the
  findings to that path — inside the mission folder, not /tmp — and cites
  `T001-notes.md:1` (or the file:line of the key claim) in its report.
- **Reviewer**: its working directory is the mission folder, so it reads
  `T001-notes.md` directly with Read offset/limit and Grep. No retry, no
  "cannot check the file": the check succeeds on the first review.

No new permission, no `--add-dir`, no restart: the Reviewer's read surface is
unchanged (mission folder only), which is why the task passes without a
wider read surface.

## 6. Fresh-session retry input budget

The retry input for a fresh session must be small enough to prefill quickly
(~40–60 s at 250 tok/s) and carry enough context to act on the review.

| Component | Source | Size (chars) | ~Tokens |
|---|---|---|---|
| Task id + title | Build Worker Run Input (l3964) | ~100 | ~25 |
| Task acceptance criteria | `task.acceptance_criteria` (per-task) | ~1,000–2,100 | ~250–525 |
| Review feedback | `s.review_feedback` | ~500–1,500 | 125–375 |
| Previous worker report (truncated) | `previous.worker_output`, 16,000-char cap (notes:17) | ≤16,000 | ≤4,000 |
| Digest (newest-first findings) | `session_digest`, `max_chars=16,000` (l1275; API default 12,000, notes:128,163) | ≤16,000 | ≤4,000 |
| Mission acceptance criteria (item 4) | `mission.acceptance_criteria` + objective (1,366–2,074 chars, §4.2) | ~1,400–2,100 | ~350–525 |
| **Total (sum of the rows)** | | **≈3,000–37,800** (upper bound 100 + 2,100 + 1,500 + 16,000 + 16,000 + 2,100; lower bound with both 16,000-char items empty: 100 + 1,000 + 500 + 1,400) | **≈750–9,450** (upper bound 25 + 525 + 375 + 4,000 + 4,000 + 525) |

At 250 tok/s the upper bound is 9,450 / 250 ≈ **38 s prefill** (the lower bound is about 3 s). Below the 10–15K target. The digest
budget is the main knob: the workflow requests `max_chars=16,000`
(Get Session Digest, l1275); the API default is 12,000 (notes:128, 163).
Reducing the digest to 12,000 saves ~1,000 tokens (~4 s) but drops some of the
newest findings. The previous-report truncation (16,000 chars) is the largest
single component; reducing it to 8,000 saves ~2,000 tokens (~8 s) but risks
losing report context the review depends on.

**Recommended budget:** keep the digest at the workflow's 16,000 chars, the
previous report at 16,000 chars, and include the mission criteria. Total
≤ 9.5K tokens (the sum of the upper bounds above), ≤ ~38 s prefill —
comfortably under the 6m07s–9m34s compaction it replaces.

## Summary of switches and defaults

| Item | Switch (proposed) | Default | Where set |
|---|---|---|---|
| 1. Fresh-session retry | `CLAUDE_RUNNER_RETRY_FRESH_SESSION` (per-provider bool); workflow flag `retry_fresh_session` | `false` (current compact-then-resume) | `PROVIDER_RUNNER_KEYS` / `_providers` (config.py:151, 279); `ProviderProfile.retry_fresh_session`; T009 patch, §5 |
| 2. Microcompact / tool-result clearing | N/A — not applicable for local provider | N/A | — (see §2.2) |
| 3. Smaller context | N/A — not worth it (see §3.3) | Current 98,304 window (probed, notes:204) / 67.66% trigger (≈66.5K) | `CLAUDE_CODE_MAX_CONTEXT_TOKENS`, `COMPACT_PCT` |
| 4. Mission criteria in Worker input | `worker_mission_criteria` (bool) + `mission_criteria_chars` (number) in the flowing state/item | `false` / `1600` (default off; always include when enabled) | No existing source — see §6.6 for the exact edit |
| 5. Reviewer result access | `RESULT PLACEMENT RULE` paragraph appended to the `architect` and `worker` rows of the `agent_prompts` table (opt-in; see §5.3 for the exact text and enable step) | off by default (the shipped `agent_prompts.example.csv` rows do not carry the rule) | `agent_prompts` data table (mission); `agent_prompts.example.csv` is the template; Reviewer unchanged (`config.py:326`) |

## 5. T009 workflow patch: fresh-session retry switch in the workflows

Patch script: `/tmp/ahawr-patch/patch_retry.py` (outside the repository; run with
`python3 patch_retry.py`, re-check with `--validate`; no n8n import).

### 5.1 Node-level diff

Pre-patch backups: `/tmp/ahawr-patch/AHAWR.orig.json`,
`/tmp/ahawr-patch/Manager.orig.json`. Node count unchanged (88, 34), the
`connections` objects byte-identical, and the only changed nodes:

| File | Node | id | Changed? |
|---|---|---|---|
| AHAWR_v13_ClaudeCode.json | Build Worker Run Input | `7a280585-c94b-4f77-a4e2-0b105bf23bb1` | yes |
| Claude_Code_Run_Manager_v1.json | Normalize Request | `488fc68c-9057-45ef-a4c7-556a6421947a` | yes |
| Claude_Code_Run_Manager_v1.json | Start / Resume Run | `1ad98af5-c3c0-445c-afa7-80453d18f2ad` | yes |

### 5.2 `Build Worker Run Input` (AHAWR)

`freshRetry`, `retryDigest` and `retryInput` are computed **at top level**,
outside the `fullInput` IIFE (after `resumeText`, before `fullInput`).
`freshRetry` is the flag plus a non-empty `worker_session_id` plus a
non-empty `review_feedback` (a review retry), **not** the `unfinished` status:

```js
const freshRetry = Boolean(s.retry_fresh_session ?? false)
  && String(s.worker_session_id ?? '').trim() !== ''
  && String(s.review_feedback ?? '').trim() !== '';
const digest = String(s.previous_session_digest ?? '').trim();
const retryDigest = digest.length > Number(s.retry_input_chars ?? 16000)
  ? digest.slice(0, Number(s.retry_input_chars ?? 16000))
    + '\n[previous session digest trimmed to the retry budget]' : digest;
const retryTask = (Array.isArray(s.tasks) ? s.tasks : [])[Number(s.task_index ?? 0)] ?? {};
const retryInput = freshRetry ? [
  'FRESH SESSION RETRY — continue in a new session from the digest below.',
  'Do NOT restart the task from scratch.',
  `TASK ${String(retryTask.id ?? '')}: ${String(retryTask.title ?? '')}`,
  '', 'ACCEPTANCE CRITERIA:',
  ((retryTask.acceptance_criteria ?? []).map((x, i) => `${i + 1}. ${x}`).join('\n')),
  '', 'PREVIOUS REVIEW FEEDBACK:', String(s.review_feedback ?? ''),
  '', 'PREVIOUS REPORT:', String(previous.worker_output ?? '').trim(),
  '', 'PREVIOUS SESSION DIGEST (newest last):', retryDigest || '(not available)'
].join('\n') : '';
```

The block uses `retryTask` (resolved from `s.tasks`/`s.task_index` at top level)
rather than the IIFE-local `tasks` variable, so it does not depend on the
`fullInput` IIFE's scope.

The returned json steers `input` and `session_id` by the flag and carries
the flag and its inputs (flag off → the pre-patch expressions exactly):

```js
return { json: {
  role: "worker",
  run_id: (String(s.worker_run_id ?? '')),
  input: freshRetry ? retryInput : (unfinished ? resumeText : fullInput),
  session_id: freshRetry ? '' : String(s.worker_session_id ?? ''),
  retry_fresh_session: Boolean(s.retry_fresh_session ?? false),
  retry_input_chars: Number.isFinite(Number(s.retry_input_chars ?? NaN)) ? Number(s.retry_input_chars) : -1,
  previous_session_digest: String(s.previous_session_digest ?? ''),
  resume_input: resumeText,
  // ... unchanged fields
} };
```

### 5.3 `Normalize Request` (Run Manager)

The state gained:
- `retry_fresh_session: Boolean(i.retry_fresh_session ?? false)`,
- `retry_input_chars: Number.isFinite(Number(i.retry_input_chars ?? NaN)) ? Number(i.retry_input_chars) : -1`,
- `previous_session_digest: String(i.previous_session_digest ?? '')`.

### 5.4 `Start / Resume Run` (Run Manager)

- `const freshRetry = Boolean($json.retry_fresh_session ?? false) && Boolean(sid) && String($json.input ?? '').startsWith('FRESH SESSION RETRY')`:
  true only when AHAWR has already built the full retry text (task, criteria, review, report, digest).
  A real fresh retry reaches this node with an empty `sid`, so the condition is a guard: an interrupted
  resume (no review feedback, session set) is never replaced by a digest-only input and keeps the usual resume text.
- `input: freshRetry ? String($json.input) : (resuming ? resumeText : ...)` — identical to the pre-patch
  expression when the flag is off.
- `if (sid && !freshRetry) body.session_id = sid;` — unchanged semantics when the flag is off.
  The runner applies its own digest cut (`cut_retry_input`, runs.py:37–53).

### 5.5 Defaults and behaviour

All three flags default to off (`?? false` / `?? 16000`): with the flag unset
every patched expression reduces to the pre-patch value, so the request is
byte-for-byte the compact-then-resume one the runner already handles.
`freshRetry` is true only when `s.retry_fresh_session` is set, the session
exists, **and** there is review feedback (a review retry) — the `unfinished`
status alone is no longer sufficient. The runner-side switch is
`CLAUDE_RUNNER_RETRY_FRESH_SESSION`
(`ProviderProfile.retry_fresh_session`, `retry_fresh_session_for`); the workflow
flag rides on the request body and is the caller-side switch. The digest input
(`previous_session_digest`) is supplied by an upstream node (e.g. a future
`Get Session Digest` step); until then it is empty and the fresh-retry input is
digest-free.

### 5.6 Script output

`--validate` re-run of the revised script (exit 0). The validate now also
executes the `Build Worker Run Input` jsCode under `node` with `$json`/`$`/
`previous` stubs, asserting that with the flag unset the returned
`session_id`/`retry_fresh_session` match the pre-patch values, and with the
flag set (plus `review_feedback`) the returned `session_id` is `''` and
`input` starts with `FRESH SESSION RETRY`.

```
check: AHAWR_v13_ClaudeCode.json loads with json.load: OK
check: Claude_Code_Run_Manager_v1.json loads with json.load: OK
check: node 'Build Worker Run Input' exists: OK
check: node 'Normalize Request' exists: OK
check: node 'Start / Resume Run' exists: OK
check: connections into 'Build Worker Run Input': [('Load Previous Worker Report', 0), ('Resume Worker?', 0)]
check: connections into 'Normalize Request': [('When Executed by Parent Workflow', 0)]
check: connections into 'Start / Resume Run': [('Compression Target?', 0), ('Has Session?', 0)]
check: node code (flag off) returns pre-patch session_id and retry_fresh_session=false: OK
check: node code (flag on) returns empty session_id and FRESH SESSION RETRY input: OK
check: freshRetry/retryDigest/retryInput computed at top level in Build Worker Run Input: OK
check: returned json steers input/session_id and carries retry_fresh_session, retry_input_chars, previous_session_digest: OK
check: flag-off expressions match the pre-patch ones (Build Worker Run Input): OK
check: flag-off expressions match the pre-patch ones (Run Manager): OK
validate: OK
```

The patch run itself prints, after writing both files: "loads with json.load
after patch", "node ... still present", "connections into ... unchanged", the
two node-execution checks (flag off / flag on), and "default (flag unset)
keeps the current behaviour" for both workflows, then
"patch applied and all checks passed" (exit 0).

## 6. T010 workflow patch: mission acceptance criteria block

Patch script: `/tmp/ahawr-patch/patch_mission_criteria.py` (outside the
repository; run with `python3 patch_mission_criteria.py`, re-check with
`--validate`, add `--execute` to also run the two nodes' jsCode under
`node` with `$json`/`$`/`previous` stubs).

### 6.1 Node-level diff

Pre-patch backup: `/tmp/ahawr-patch/prepatch.json` (current repo file with the
two nodes' jsCode reverted to the pre-patch text). Node count unchanged, the
`connections` object byte-identical, and only the two target nodes change:

| File | Node | id | Changed? |
|---|---|---|---|
| AHAWR_v13_ClaudeCode.json | Build Worker Run Input | `7a280585-c94b-4f77-a4e2-0b105bf23bb1` | yes |
| AHAWR_v13_ClaudeCode.json | Build Reviewer Run Input | `64aeb5f4-db87-4026-b8d2-212c2b7f05e0` | yes |

### 6.2 `Build Worker Run Input`

`missionCriteria` is computed **at top level**, after the T009
`retryDigest`/`retryInput` block and before `fullInput`. When the flag is off
(`s.worker_mission_criteria` unset), the IIFE returns `''` and
`fullInput` is byte-for-byte the unpatched input. When on, it appends a
numbered list from `Mission.acceptance_criteria` plus a one-line mission goal
(`Mission.objective`), truncated to `mission_criteria_chars` (default 1600):

```js
const missionCriteria = (() => {
  if (!Boolean(s.worker_mission_criteria ?? false)) return '';
  const m = $('Mission').first().json ?? {};
  const list = (Array.isArray(m.acceptance_criteria) ? m.acceptance_criteria : []).map(String);
  if (!list.length) return '';
  const goal = String(m.objective ?? '').trim();
  const budget = Number(s.mission_criteria_chars ?? 1600);
  let text = 'MISSION ACCEPTANCE CRITERIA:' + '\n' + list.map((x, i) => `${i + 1}. ${x}`).join('\n');
  if (goal) text += '\n\nMISSION GOAL: ' + goal;
  if (budget > 0 && text.length > budget) text = text.slice(0, budget) + '\n…(mission criteria block truncated to the budget)';
  return text;
})();
```

Inside the `fullInput` array, the new element is a plain `missionCriteria`
(the Worker array already ends with `.filter(Boolean).join('\n')`, so an
empty string contributes nothing when off):

```js
    'ACCEPTANCE CRITERIA:',
    (task.acceptance_criteria ?? []).map((x, i) => `${i + 1}. ${x}`).join('\n'),
    '',
    missionCriteria,
    'VERIFICATION:',
```

### 6.3 `Build Reviewer Run Input`

The Reviewer uses `.join('\n')` without `.filter(Boolean)`, so the block is
added as a **spread conditional** inside the input IIFE (after
`workerOutput`, before the array). When off, the spread is empty and the
input is byte-for-byte the unpatched reviewer input:

```js
  const missionCriteria = (() => {
    if (!Boolean($json.worker_mission_criteria ?? false)) return '';
    const m = $('Mission').first().json ?? {};
    const list = (Array.isArray(m.acceptance_criteria) ? m.acceptance_criteria : []).map(String);
    if (!list.length) return '';
    const goal = String(m.objective ?? '').trim();
    const budget = Number($json.mission_criteria_chars ?? 1600);
    let text = 'MISSION ACCEPTANCE CRITERIA:' + '\n' + list.map((x, i) => `${i + 1}. ${x}`).join('\n');
    if (goal) text += '\n\nMISSION GOAL: ' + goal;
    if (budget > 0 && text.length > budget) text = text.slice(0, budget) + '\n…(mission criteria block truncated to the budget)';
    return text;
  })();
```

```js
    'ACCEPTANCE CRITERIA:',
    (Array.isArray(task.acceptance_criteria) ? task.acceptance_criteria : []).map((x, i) => `${i + 1}. ${x}`).join('\n'),
    '',
    ...(missionCriteria ? [missionCriteria, ''] : []),
    'VERIFICATION:',
```

### 6.4 Example assembled Worker input (flag on)

With `worker_mission_criteria: true` and stub data
(`Mission.acceptance_criteria = ["c1","c2","c3"]`,
`Mission.objective = "build the thing"`, one task `T1`):

```
WP
TASK T1: t
OBJECTIVE:
o
ACCEPTANCE CRITERIA:
1. a1
2. a2
MISSION ACCEPTANCE CRITERIA:
1. c1
2. c2
3. c3

MISSION GOAL: build the thing
VERIFICATION:
SCOPE:
```

The `MISSION ACCEPTANCE CRITERIA:` block sits between the task's own
`ACCEPTANCE CRITERIA:` and the `VERIFICATION:` section. With the flag off the
input is exactly the pre-patch input (the `missionCriteria` element is
empty and filtered out).

### 6.5 Token and prefill growth

The block adds one numbered list of mission-level criteria plus a one-line
mission goal. Measured mission `acceptance_criteria_json` sizes (see §4.2)
translate to **~340–520 tokens, ~1.4–2.1 s of prefill at 250 tok/s**.
This is negligible against the 36–77 s prefill of the full retry input.

### 6.6 Switch name and how to turn it on

**No existing node sets `worker_mission_criteria` or `mission_criteria_chars`.**
`grep -n "worker_mission_criteria" AHAWR_v13_ClaudeCode.json` matches only the
two patched nodes' jsCode (the `?? false` / `?? 1600` defaults); no other node
emits the field, and the `hermes_config` data-table row / `Constants` node
does not pass it through. The runner-side env var
`CLAUDE_RUNNER_WORKER_MISSION_CRITERIA` is **not** parsed in `config.py:279`
and does **not** map to any `ProviderProfile` field — that claim was incorrect.

To enable the block, an operator must add the field to the flowing state/item
that reaches each node:

**Worker path — how `s` is built:**
- `s` is `$json` (the item from `Prepare Current Task`, which does
  `{ ...$json, execution_route, current_task, current_task_id, worker_prompt }`
  at `l153`) **or** `$('Attach Worker Context').last().json` (the resume path,
  which does `{ ...base, retrieval_worker_context, ... }` at `l3835`).
- Neither source currently carries `worker_mission_criteria`.
- **Exact edit:** add `worker_mission_criteria: true` (and optionally
  `mission_criteria_chars: 1600`) to the state-building node. The minimal
  change is in `Prepare Current Task` (`l153`):
  ```js
  return [{ json: { ...$json, execution_route: 'run_task', current_task: task,
    current_task_id: task.id, worker_prompt: workerPrompt,
    worker_mission_criteria: true, mission_criteria_chars: 1600 } }];
  ```
  For the resume path, add the same two fields to `Attach Worker Context`
  (`l3835`):
  ```js
  return [{ json: { ...base, retrieval_worker_context: ok ? resp.context : '',
    /* ...existing fields... */
    worker_mission_criteria: true, mission_criteria_chars: 1600 } }];
  ```
  Alternatively, source the values from the `hermes_config` row: add two
  columns to the `claude-code` profile row, pass them through the `Constants`
  node (`l540`), and read them from `$('Constants').first().json` in
  `Prepare Current Task` / `Attach Worker Context`.

**Reviewer path — how `$json` is built:**
- `$json` is the item from `Reviewer State` (a `set` node at `l599`) which
  assigns `current_task_id`, `task_index`, `task_attempt`, `state_key`,
  `reviewer_run_id`, `reviewer_session_id`, `reviewer_provider_retry_count`.
  It does **not** assign `worker_mission_criteria`.
- **Exact edit:** add an assignment to the `Reviewer State` node:
  ```
  name: worker_mission_criteria
  value: ={{ true }}        (or ={{ $('Constants').first().json.worker_mission_criteria }}
                             if sourced from the hermes_config row)
  type: boolean
  ```
  and similarly for `mission_criteria_chars` (type `number`, default `1600`).

When the switch is off (default), both nodes emit byte-for-byte the unpatched
input — the `missionCriteria` IIFE returns `''` and contributes nothing.

### 6.7 Script output

`python3 patch_mission_criteria.py` (apply + validate + execute, exit 0).
The patch is applied against a pre-patch working copy; the checks below
validate the result against that original.

```
check: worker anchor: anchor matches exactly once (found 1): OK
check: worker array: anchor matches exactly once (found 1): OK
check: reviewer anchor: anchor matches exactly once (found 1): OK
check: reviewer array: anchor matches exactly once (found 1): OK
check: AHAWR_v13_ClaudeCode.json loads with json.load: OK: OK
check: node 'Build Worker Run Input' exists: OK: OK
check: node 'Build Reviewer Run Input' exists: OK: OK
check: connections into 'Build Worker Run Input' unchanged: ['Load Previous Worker Report', 'Resume Worker?']: OK
check: connections into 'Build Reviewer Run Input' unchanged: ['Attach Reviewer Context']: OK
check: only the two nodes changed (all other nodes byte-identical): OK: OK
check: connections object byte-identical: OK: OK
check: worker flag-off input equals pre-patch input: OK: OK
check: worker flag-on input contains MISSION ACCEPTANCE CRITERIA block: OK: OK
check: reviewer flag-off input equals pre-patch input: OK: OK
check: reviewer flag-on input contains MISSION ACCEPTANCE CRITERIA block: OK: OK
check: writes file and json.load succeeds after patch: OK
patch applied and all checks passed
validate: OK
```

A `--validate` re-run on the already-patched file skips the anchor checks
(the anchors are absent after patching) and prints the remaining structural
checks plus the four node-execution checks, ending with `validate: OK`.

## 7. T013: full checks (claude-runner + workflow validation)

Environment: `/opt/claude-runner/venv`, Python 3.11. No service was restarted
or rebuilt.

### 7.1 claude-runner — four checks, all pass

Run from `claude-runner/` with the venv. All four exit 0.

```
$ pytest -q tests
128 passed, 1 warning in 27.69s   (coverage 96.94 % ≥ 80 %)
$ ruff check src tests
All checks passed!
$ ruff format --check src tests
28 files already formatted
$ mypy src
Success: no issues found in 17 source files
```

Fixes applied to make the checks pass (in `claude-runner/`):
- `src/claude_runner/runs.py:45` — removed the unused `keep` local (F841); it is
  recomputed only where needed and was never read.
- `src/claude_runner/config.py:476` — wrapped the long `retry_input_chars`
  argument across lines (E501; ruff-format target).
- `tests/conftest.py` — moved the `claude_runner.api`/`config` imports above
  the `sys.path` guard with `# noqa: E402`, so the source-tree precedence is
  preserved while the E402 import-placement lint passes.
- `tests/test_config_cli.py` — added `from pathlib import Path` (F821: `Path`
  was used in a `tmp_path: Path` annotation without being imported).

### 7.2 Workflow validation — `--validate` re-run of both patch scripts

Both scripts are in `/tmp/ahawr-patch/`. Re-run with `--validate` only.

`python3 patch_mission_criteria.py --validate` — **exit 0**:

```
check: AHAWR_v13_ClaudeCode.json loads with json.load: OK: OK
check: node 'Build Worker Run Input' exists: OK: OK
check: node 'Build Reviewer Run Input' exists: OK: OK
check: connections into 'Build Worker Run Input' unchanged: ['Load Previous Worker Report', 'Resume Worker?']: OK
check: connections into 'Build Reviewer Run Input' unchanged: ['Attach Reviewer Context']: OK
check: only the two nodes changed (all other nodes byte-identical): OK: OK
check: connections object byte-identical: OK: OK
validate: OK
```

`python3 patch_retry.py --validate` — **exit 0**:

```
check: AHAWR_v13_ClaudeCode.json loads with json.load: OK
check: Claude_Code_Run_Manager_v1.json loads with json.load: OK
check: node 'Build Worker Run Input' exists: OK
check: node 'Normalize Request' exists: OK
check: node 'Start / Resume Run' exists: OK
check: connections into 'Build Worker Run Input': [('Load Previous Worker Report', 0), ('Resume Worker?', 0)]
check: connections into 'Normalize Request': [('When Executed by Parent Workflow', 0)]
check: connections into 'Start / Resume Run': [('Compression Target?', 0), ('Has Session?', 0)]
check: node code (flag off) returns pre-patch session_id and retry_fresh_session=false: OK
check: node code (flag on) returns empty session_id and FRESH SESSION RETRY input: OK
check: freshRetry/retryDigest/retryInput computed at top level in Build Worker Run Input: OK
check: returned json steers input/session_id and carries retry_fresh_session, retry_input_chars, previous_session_digest: OK
check: flag-off expressions match the pre-patch ones (Build Worker Run Input): OK
check: flag-off expressions match the pre-patch ones (Run Manager): OK
validate: OK
```

The validator previously asserted the **earlier digest-rebuild** tokens
(`const freshRetry = Boolean($json.retry_fresh_session ?? false) &&
Boolean(sid);` and `input: freshRetry ? retryInput : ...`), which no longer
match the current `Start / Resume Run` node. Its `validate_manager` tokens
(`/tmp/ahawr-patch/patch_retry.py:490-493`) were updated to the **pass-through**
variant documented in §5.4 —
`const freshRetry = Boolean($json.retry_fresh_session ?? false) &&
Boolean(sid)` newline `    && String($json.input ?? '').startsWith('FRESH
SESSION RETRY')` and
`input: freshRetry ? String($json.input) : (resuming ? resumeText :
String($json.input || $json.resume_input || ''))` — while the flag-off checks
(`if (sid && !freshRetry) body.session_id = sid;` and the pre-patch `resuming`
line) were left unchanged. The AHAWR `Build Worker Run Input` node and the
manager `Normalize Request` node were never mismatched; only the `Start /
Resume Run` token check had failed before this fix.

## Final report

### Files changed (from `git status` / `git diff --stat`, read-only)

Working tree at report time (branch `claude/ahawr-context-retrieval-layer-uq2ffv`):

**Modified (staged):**

| File | Status | Diff summary |
|---|---|---|
| claude-runner/src/claude_runner/dashboard.html | staged | Auto/Light/Dark theme switcher in the sidebar |

**Modified (unstaged):**

| File | Lines (±) | Diff summary |
|---|---|---|
| .env.example | +17 | Documented new env vars: `CLAUDE_RUNNER_RETRY_FRESH_SESSION`, `CLAUDE_RUNNER_RETRY_INPUT_CHARS`, `CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS`, `CLAUDE_RUNNER_WORKER_APPEND_SYSTEM_PROMPT`, `CLAUDE_RUNNER_ARCHITECT_APPEND_SYSTEM_PROMPT`, `CLAUDE_RUNNER_SEARCH_FIRST_EVERY`, `AHAWR_SEARCH_FIRST` |
| AHAWR_v13_ClaudeCode.json | +143/−6 | T009 fresh-session retry patch (Build Worker Run Input) + T010 mission-criteria patch (Build Worker Run Input, Build Reviewer Run Input) |
| CHANGELOG.md | +99 | Changelog entries for all changes (0.2.0) |
| Claude_Code_Run_Manager_v1.json | +3/−3 (6 total) | T009 fresh-session retry patch (Normalize Request, Start / Resume Run) |
| README.md | +114/−2 (116 total) | Updated documentation |
| agent_prompts.example.csv | +1 | Result placement rule example |
| claude-runner/README.md | +64/−3 (67 total) | Theme button, reviewer Bash, search-first hook, result placement rule |
| claude-runner/pyproject.toml | +1/−1 (2 total) | Version bump |
| claude-runner/src/claude_runner/api.py | +1/−1 (2 total) | Minor adjustment |
| claude-runner/src/claude_runner/claude_cli.py | +52/−3 (55 total) | Search-first PreToolUse hook settings path, compact hook command |
| claude-runner/src/claude_runner/config.py | +126/−2 (128 total) | `search_first`, `retry_fresh_session`, `retry_input_chars` settings; reviewer Bash allow/deny; `_opt_bool` parser; `retry_fresh_session_for`/`search_first_for` |
| claude-runner/src/claude_runner/runs.py | +46/−3 (49 total) | `cut_retry_input`, fresh-session retry logic in `_bind`, skip compaction when fresh retry is on |
| claude-runner/src/claude_runner/search_cli.py | +121/−23 (144 total) | `ahawr-search` CLI improvements: `--query`/`-q`, better error messages |
| claude-runner/tests/conftest.py | +8/−2 (10 total) | Import ordering, `src/` first on `sys.path` |
| claude-runner/tests/test_config_cli.py | +105 | Tests for new config options |
| claude-runner/tests/test_dashboard.py | +25 | Theme switcher test |
| claude-runner/tests/test_runs.py | +128 | Fresh-session retry tests |
| claude-runner/tests/test_search_cli.py | +70 | Search CLI tests |
| docker-compose.yml | +2 | `AHAWR_SEARCH_FIRST=1` for LOCAL provider |

**Untracked (new files):**

| File | Purpose |
|---|---|
| AHAWR_Status_Bot.json | Telegram status bot workflow |
| claude-runner/src/claude_runner/search_hook.py | PreToolUse hook for search-first |
| claude-runner/tests/test_search_hook.py | Tests for search hook |
| docs/ahawr-cheaper-retries-notes.md | T002 measurement notes (source of all numbers) |
| docs/ahawr-cheaper-retries.md | This document |
| docs/telegram/ | Telegram docs |

Total: 19 modified files, 1126 insertions, 49 deletions.

### Apply commands (listed, not executed)

**1. Import the patched workflows (n8n):**

```
# In n8n: Workflows → Import (or Import via API)
# AHAWR_v13_ClaudeCode.json
# Claude_Code_Run_Manager_v1.json
```

**2. Rebuild the runner (docker compose, from /d/n8n):**

```
# /d/n8n
docker compose build claude-runner
docker compose up -d claude-runner
docker compose exec claude-runner curl -s http://127.0.0.1:8700/health
```

**3. Set the `.env` values (in /d/n8n/.env, next to docker-compose.yml; take effect after the `up -d` above):**

```
# Fresh-session retry (off by default; enable to skip pre-retry /compact)
CLAUDE_RUNNER_RETRY_FRESH_SESSION=1

# Character budget for the fresh-session retry input (default 32000)
CLAUDE_RUNNER_RETRY_INPUT_CHARS=32000

# Search-first hook (docker-compose sets AHAWR_SEARCH_FIRST=1 for LOCAL)
AHAWR_SEARCH_FIRST=1
CLAUDE_RUNNER_SEARCH_FIRST_EVERY=6

# Reviewer read-only Bash (shipped default; override only to narrow)
# CLAUDE_RUNNER_REVIEWER_ALLOWED_TOOLS=Bash(ahawr-search *),Bash(grep *),...

# Opt-in result placement rule (append to the role's system prompt)
CLAUDE_RUNNER_WORKER_APPEND_SYSTEM_PROMPT=RESULT PLACEMENT RULE: place every file the Reviewer must verify (notes, artifacts, logs) in the mission working directory or a subfolder of it — never in /tmp or elsewhere — and cite its exact path in your report. The Reviewer reads only the mission working directory.
CLAUDE_RUNNER_ARCHITECT_APPEND_SYSTEM_PROMPT=RESULT PLACEMENT RULE: name in each task's `scope` or `acceptance_criteria` the exact path(s) — inside the mission working directory or a subfolder of it — where the Worker must place the files the Reviewer will verify (notes, artifacts, logs). Do not place those files in /tmp or outside the mission folder: the Reviewer reads only the mission working directory.
```

### Expected time saving for a mission like `ahawr-fast-compaction`

**Formula (from §1.1):**

$$
\text{saved per retry} = t_{\text{compaction}} - t_{\text{fresh prefill}}
$$

where:
- $t_{\text{compaction}}$ = pre-retry compaction duration (measured, notes:270): **366–573 s** (6m07s–9m34s)
- $t_{\text{fresh prefill}}$ = prefill of the fresh retry input at ~250 tok/s: **40–60 s** (10–15K tokens / 250 tok/s)

**Per retry:**

$$
\text{saved} = (366 - 60) / 60 \approx 5.1 \text{ to } (573 - 40) / 60 \approx 8.9 \text{ min per retry}
$$

**For a full mission like `ahawr-fast-compaction`:**

The 4 measured pre-retry compactions total **31m50s** (notes:234–239). Replacing each with a fresh prefill of 40–60 s:

$$
31\text{m50s} - 4 \times (40\text{–}60\text{ s}) = 28\text{–}29\text{ min per mission}
$$

**Inputs from T002 (`docs/ahawr-cheaper-retries-notes.md` §4):**
- 4 fast-compaction-era compactions: `cmp_9c9e19e8` 8m31s, `cmp_fa43f633` 6m54s, `cmp_0218a78c` 6m51s, `cmp_7f3d625c` 9m34s → total 31m50s (notes:234–239)
- Fresh retry input size: ~10–15K tokens (notes §4.6, mission text line 7)
- Prefill rate: 194–276 tok/s (notes §4.6 row 4); rounded to ~250 tok/s for the estimate
- Compaction duration range: 366–573 s (notes:270)
