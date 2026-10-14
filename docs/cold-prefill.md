## Part A: slot persistence (source analysis)

> Full standalone copy: docs/cold-prefill/partA-slot-persistence.md (same sections 1-8).


Source tree analyzed: `D:/OpenAIProjects/llama.cpp` (working tree has `src/`, `tools/`, `common/` deleted;
all files quoted below were read from `git show HEAD:<path>` of that repo, so the line numbers are
`HEAD` line numbers).

### 1. What the slot save/restore/erase endpoints do

`POST /slots/{id}?action=save|restore|erase` is dispatched in `tools/server/server-context.cpp:4660-4669`
(`action == "save"` -> `handle_slots_save`, `"restore"` -> `handle_slots_restore`, `"erase"` -> `handle_slots_erase`),
and the server refuses to handle any of them when `--slot-save-path` was not given:
`tools/server/server-context.cpp:4645-4646` ("This server does not support slots action. Start it with `--slot-save-path`").

The `--slot-save-path` option is parsed in `common/arg.cpp:3575-3584` and stored as
`params.slot_save_path` (`common/common.h:675`). The save/restore handlers build the full file path as
`params.slot_save_path + filename` (`tools/server/server-context.cpp:5170`, `:5206`) and enqueue a task
(`:5176-5178`, `:5212-5214`).

- **save** (`SERVER_TASK_TYPE_SLOT_SAVE`, `tools/server/server-context.cpp:2429-2478`):
  - serializes the slot's prompt tokens (`slot->prompt.tokens.serialize()`, `:2451`) into `packed`;
  - writes the file via `llama_state_seq_save_file(ctx_tgt, filepath, slot->id, ...)` (`:2458-2460`);
  - the public wrapper `llama_state_seq_save_file` is `src/llama-context.cpp:4094-4103`, calling
    `llama_context::state_seq_save_file` (`:3154-3172`);
  - response fields `n_tokens`, `n_bytes`, `t_ms` (`:2474-2476`).
- **restore** (`SERVER_TASK_TYPE_SLOT_RESTORE`, `tools/server/server-context.cpp:2479-2543`):
  - first reads the file to get the token count (`llama_state_seq_load_file` with `nullptr` buffer, `:2503`);
  - second read actually restores the memory and the token array (`:2506`, via
    `llama_state_seq_load_file` `src/llama-context.cpp:4105-4114` -> `llama_context::state_seq_load_file` `:3100-3152`);
  - then deserializes the packed `server_tokens` (`server_tokens::deserialize`, `:2513`, implementation in
    `tools/server/server-common.cpp:575`), checks `restored.size() <= slot->n_ctx` (`:2515-2517`), validates tokens
    (`:2519-2521`), clears the slot and installs the restored tokens (`slot->prompt.clear(); slot->prompt.tokens = std::move(restored)`, `:2523-2524`).
- **erase** (`SERVER_TASK_TYPE_SLOT_ERASE`, `tools/server/server-context.cpp:2544-2569`): counts
  `slot->prompt.tokens.size()` (`:2560`) and calls `slot->prompt_clear()` (`:2562`). `server_slot::prompt_clear`
  does `mem.seq_rm(id, -1, -1)` and clears the prompt (`tools/server/server-context.cpp:288-294`).

Note: restore does not check whether the new prompt will match the restored tokens' prefix. That matching
happens later, at task start (section 3).

### 2. Slot save file format

Written by `llama_context::state_seq_save_file` (`src/llama-context.cpp:3154-3172`):

```
[u32] LLAMA_STATE_SEQ_MAGIC          // 0x67677371 ('ggsq'), include/llama.h:43,48
[u32] LLAMA_STATE_SEQ_VERSION        // 2, include/llama.h:49
[u32] n_token_count
[4 * n_token_count bytes] packed prompt tokens (packed `server_tokens` format, see below)
[state stream]                  // state_seq_write_data (see below)
```

Read back by `llama_context::state_seq_load_file` (`src/llama-context.cpp:3100-3152`): magic/version
checked (`:3104-3112`), token count read with capacity check (`:3114-3136`), then
`state_seq_read_data(io, seq_id, 0)` with a size-consistency assertion
(`GGML_ASSERT(nread + 4*3 + 4*n_tokens == file.tell())`, `:3142-3148`).

The packed token payload: `server_tokens::deserialize` (`tools/server/server-common.cpp:575-...`)
handles two encodings — a plain `llama_token` list (older servers) or a packed format starting with
`LLAMA_TOKEN_NULL` + `SERVER_TOKENS_STATE_VERSION` + token vector + optional mtmd media chunks.

The state stream is produced by `llama_context::state_seq_write_data` (`src/llama-context.cpp:3220-3226`):

```
state_seq_write_data -> memory->state_write(io, seq_id, flags)      // llama-context.cpp:3222
```

with `flags = 0` (`LLAMA_STATE_SEQ_FLAGS_NONE`, `include/llama.h:897`) in both the save path
(`:3166`) and the restore path (`:3142`).

### 3. What the state stream contains: KV and recurrent (GDN) state

`memory` is the model's memory module. For a hybrid model (attention KV + recurrent GDN) it is
`llama_memory_hybrid`, whose `state_write`/`state_read` write both sub-caches:

- `src/llama-memory-hybrid.cpp:190-195` — `state_write`:
  ```
  if ((flags & LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY) == 0) mem_attn->state_write(io, seq_id, flags);
  mem_recr->state_write(io, seq_id, flags);
  ```
- `src/llama-memory-hybrid.cpp:197-202` — `state_read` (same structure).

So with `flags = 0`, **both** the attention KV cache and the recurrent cache are written/restored:

- **KV**: `llama_kv_cache::state_write` (`src/llama-kv-cache.cpp:1969-2037`) writes per-stream cell counts,
  cell position metadata and cell data (`state_write_meta` `:2034` / `state_write_data` `:2035`,
  definitions at `:2088`, `:2121`); read back by `llama_kv_cache::state_read` (`:2039-2087`).
- **Recurrent (GDN)**: `llama_memory_recurrent::state_write` (`src/llama-memory-recurrent.cpp:743-822`)
  writes `cell_count`, per-cell `pos`/`n_seq_id` metadata (`state_write_meta` `:855-872`) and the
  per-layer R tensors (and S tensors, with a transposed-S variant) (`state_write_data` `:874-956`).
  `state_read` (`:824-853`) restores them, resets the recurrent rollback index to 0
  (`set_rs_idx(seq_id, 0)`, `:850-852`), and on failure clears the state and throws
  (`:840-848`).

Conclusion: **the slot save file carries the full attention KV cache plus the full recurrent (GDN) state**
for the slot's sequence id. This is the same mechanism the server's in-memory prompt cache uses
(`llama_state_seq_get_data_ext`/`llama_state_seq_set_data_ext`, `src/llama-context.cpp:4083-4092`,
called from `server_slot::prompt_save` `tools/server/server-context.cpp:258-277` and
`server_prompt_cache::load` `tools/server/server-task.cpp:1832,1850`).

### 4. Context checkpoints: not saved, not restored

Server "context checkpoints" are a separate feature, enabled by `--n-ctx-checkpoints`
(`params_base.n_ctx_checkpoints`, logged at `tools/server/server-context.cpp:1283-1287`).

- They live in `server_slot::prompt.checkpoints`, a `std::list<common_prompt_checkpoint>`
  (`tools/server/server-task.h:566-586`), each holding `data_tgt`/`data_dft`/`data_spec` byte blobs
  (`common/common.h:1137-1178`).
- Created during prompt processing by `create_checkpoint`
  (`tools/server/server-context.cpp:2209-2256`): only for completion tasks and only for models that cannot
  roll back fully (`COMMON_CONTEXT_SEQ_RM_TYPE_FULL`, `COMMON_CONTEXT_SEQ_RM_TYPE_RS`, or SWA;
  `:3344-3357`), with one checkpoint per user-message boundary / near prompt end
  (`:3434-3462`, `:3489-3512`), captured via
  `llama_state_seq_get_data_ext(..., LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY)`
  (`common/common.cpp:2290-2297`, `:2310-2315`).
- Used when a new prompt's LCP is short: the task processor searches `slot.prompt.checkpoints` in reverse
  for one whose `pos_min` covers the common prefix and restores it via
  `ckpt.load_tgt/load_dft` with `LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY`
  (`tools/server/server-context.cpp:3233-3261`), i.e. partial state restore.
- **The slot save/restore endpoints never serialize `prompt.checkpoints`.** The save handler writes only
  the token payload plus the full-state stream (`tools/server/server-context.cpp:2446-2460`); the
  restore handler only reads the token payload and full-state stream (`:2499-2524`).
  `server_prompt`'s checkpoints are cleared by `slot->prompt.clear()` during restore
  (`tools/server/server-task.h:571-574`), and nothing re-creates them from the file.

Consequence for a hybrid (KV+GDN) model: after a restore, `prompt.checkpoints` is empty. If a later
prompt diverges early (short LCP), the code takes the "do_reset" branch:
`"forcing full prompt re-processing due to lack of cache data (likely due to SWA or hybrid/recurrent memory)"`
(`tools/server/server-context.cpp:3263-3268`), i.e. `pos_next = 0; n_past = 0`. The recurrent state is
still restored from the file, but without a checkpoint there is no cheap partial re-evaluation path —
the whole prefix up to the divergence point must be re-run.

### 5. Prefix matching after a restore

Slot selection and reuse is LCP-based, purely on token arrays:

- Slot selection (`get_available_slot`, `tools/server/server-context.cpp:1454-1541`): for each idle slot,
  `lcp_len = tokens.get_common_prefix(task.tokens)` (`:1491`); a slot is chosen if
  `f_sim_cur = lcp_len/task.tokens.size() > slot_prompt_similarity` (`:1497`); if the kept fraction
  `f_keep < 0.5` the old prompt is saved into the in-memory prompt cache and the new one loaded
  (`:1512-1518`, `prompt_save`/`prompt_load` at `:1554-1557`, `:253-286`).
- In-memory prompt cache matching (`server_prompt_cache::load`, `tools/server/server-task.cpp:1793-1868`):
  best entry by `lcp = prompt.tokens.get_common_prefix(tokens_new)` (`:1794,1805`), rejected if
  `f_keep_cur < 0.25` (`:1813`), then its main/draft state blobs are written back via
  `llama_state_seq_set_data_ext` (`:1832,1850`) and the cached prompt is moved into the slot
  (`:1862-1864`).
- Per-task reuse after restore (`tools/server/server-context.cpp:3101-3175`):
  `n_past = slot.prompt.tokens.get_common_prefix(input_tokens)` (`:3103`) when `cache_prompt` is set;
  optional KV shifting for mid-prompt reuse via `n_cache_reuse`
  (`slot.mem.seq_rm/seq_add`, `:3122-3159`), gated by `llama_memory_can_shift`
  (`:3113-3115`). For hybrid memory, `llama_memory_hybrid::get_can_shift` delegates to the attention
  cache (`src/llama-memory-hybrid.cpp:133-136`); the recurrent cache is "trivially" shiftable —
  `llama_memory_recurrent::get_can_shift` returns true (`src/llama-memory-recurrent.cpp:705-708`) and
  `seq_add` only shifts the recurrent cell position (`src/llama-memory-recurrent.cpp:307-335`).

So a restored slot participates in normal LCP matching: if the next prompt shares the restored prefix,
only the divergent tail is evaluated (the unit test asserts `prompt_n == 6` vs 21 for a shared 15-token
prefix, `tools/server/tests/unit/test_slot_save.py:42-49,59-66`). If it does not share the prefix,
`n_past = 0` and (with no checkpoints) the full prompt is re-processed — the restored KV is then
discarded by `slot->prompt.clear()`/`prompt_clear` when the task replaces the prompt
(`tools/server/server-context.cpp:288-294`).

Restore failure behavior (`tools/server/server-context.cpp:2508-2528`): any of
`llama_state_seq_load_file` returning 0 ("No available space in KV cache or invalid slot save file",
`:2509`), token payload too large for the slot (`:2515-2517`), or token validation failing
(`:2519-2521`) throws; the catch clears the slot (`slot->prompt_clear()`, `:2526`) and responds
`ERROR_TYPE_INVALID_REQUEST` "Unable to restore slot: <error>" (`:2527`). The slot ends up empty;
the client must retry or send a fresh prompt.

### 6. Verdict

| Item | Saved/restored by `POST /slots/{id}?action=save|restore`? |
|---|---|
| Attention KV cache | **YES** — written via `llama_memory_hybrid::state_write` -> `llama_kv_cache::state_write` (`src/llama-memory-hybrid.cpp:190-195`, `src/llama-kv-cache.cpp:1969-2037`) with `flags = 0` (`src/llama-context.cpp:3142,3166`) |
| Recurrent (GDN) state | **YES** — same path, `llama_memory_hybrid::state_write` -> `llama_memory_recurrent::state_write` (`src/llama-memory-hybrid.cpp:190-195`, `src/llama-memory-recurrent.cpp:743-822`), incl. R/S tensors and per-cell positions; rollback index reset on load (`src/llama-memory-recurrent.cpp:850-852`) |
| Context checkpoints | **NO** — `prompt.checkpoints` is in-memory only (`tools/server/server-task.h:566-586`, `tools/server/server-context.cpp:2209-2256`); the slot save/restore handlers never serialize or deserialize them, and restore clears them (`tools/server/server-context.cpp:2523`) |

The recurrent state is fully carried, so a hybrid model can be saved/restored across a server restart
without re-running the prompt *if the next prompt keeps the same prefix*. The gap is the checkpoint
layer: without checkpoints, any prompt divergence forces a full prefix re-run
(`tools/server/server-context.cpp:3263-3268`), and the in-memory prompt cache (`--slot-save-path` file
aside) does not survive a process restart either.

### 7. Alternatives (since checkpoint persistence is missing)

1. **Keep the server warm (preferred, zero code change).** Do not restart the llama.cpp server between
   sessions; save a slot only as a backstop. The in-memory prompt cache (`--slot-save-path`'s sibling
   feature, populated at `tools/server/server-context.cpp:1512-1518`) keeps both KV and recurrent state
   of idle slots alive across tasks, and checkpoints survive while the slot lives. Restart the server
   only when the model changes.
2. **Order restarts around compaction.** If a restart is unavoidable (e.g. model swap or GPU OOM),
   compact the mission context to a short stable prefix *before* the restart, then send that prefix as
   the first prompt after restart so the restored slot's LCP covers everything needed. After restore,
   a matching prefix means only the tail is evaluated (see section 5 and
   `tools/server/tests/unit/test_slot_save.py:59-66`); a diverging prefix re-runs from token 0
   (`tools/server/server-context.cpp:3263-3268`).
3. **Enable `--n-ctx-checkpoints` for long-lived sessions.** Checkpoints give partial re-evaluation at
   user-message boundaries (`tools/server/server-context.cpp:2247-2250, 3233-3261`), which is exactly
   the case a restart + divergent prompt would otherwise pay in full prefill. They are not persisted,
   so they help only while the server stays up — combine with alternative 1.
4. **Persist checkpoints as an extension (if needed).** The checkpoint blobs are plain
   `llama_state_seq_get_data_ext` captures (`common/common.cpp:2290-2297`); a small extension could
   serialize `prompt.checkpoints` (per-checkpoint `data_tgt`/`data_dft` + `pos_min`/`pos_max`) next to
   the slot save file and restore them on load. Not implemented in this tree.

### 8. Unverified (operator check)

The following cannot be confirmed from source alone and need a GPU run:

1. **Recurrent-state size and save cost** for the actual hybrid model: `n_bytes` in the save response
   (`tools/server/server-context.cpp:2475`) and the R/S tensor sizes (`src/llama-memory-recurrent.cpp:892,914`)
   depend on the model's `n_embd_r`/`n_embd_s` and cell count; measure with a real model.
2. **End-to-end restore correctness on a hybrid model**: the unit test
   (`tools/server/tests/unit/test_slot_save.py`) uses a tiny attention-only model. Verify that a hybrid
   (KV+GDN) slot restores and continues with `timings.prompt_n` equal to the divergent tail length
   (e.g. 6 in the test) after `POST /slots/{id}?action=restore` followed by a shared-prefix prompt.
3. **Checkpoint-free divergence behavior**: confirm that after restore, a prompt diverging before the
   restored prefix indeed re-processes from token 0 (`tools/server/server-context.cpp:3263-3268`) —
   expect `prompt_n` to equal the full new prompt length.
4. **Restore failure mode with a full KV cache**: "No available space in KV cache"
   (`tools/server/server-context.cpp:2509`) should be reproducible by saving a prompt near `n_ctx` and
   restoring into a slot with a smaller remaining capacity; verify the slot is left empty and the
   error is `ERROR_TYPE_INVALID_REQUEST` (`:2527`).
5. **Device-side save path** (`LLAMA_STATE_SEQ_FLAGS_ON_DEVICE`, `include/llama.h:905-907`) is not used
   by the slot endpoints (they pass flags 0, `src/llama-context.cpp:3142,3166`); on multi-GPU setups
   verify the host-side copy does not dominate save time.

### 9. Slot file size estimate (qwen3.8-27b-gsq-rco-iq2-s-mtp)

**Unverified until an operator saves a real slot** (see step 2 below).

The file layout (section 2) is:

```
[u32] magic (0x67677371)
[u32] version
[u32] n_token_count
[4*n_token_count bytes] packed prompt tokens
[state stream] = KV part (llama_kv_cache::state_write) + recurrent part (llama_memory_recurrent::state_write)
```

Inputs and sources (GGUF = `/d/OllamaModels/Qwen3.8-27B-GSQ-RCO-IQ2_S-mtp/Qwen3.8-27B-GSQ-RCO-IQ2_S-mtp.gguf`,
metadata read without loading the model; production preset = `[qwen3.8-27b-gsq-rco-iq2-s-mtp]` section of
`/d/OpenAIProjects/llama.cpp/docker-entrypoint.sh`):

| Input | Value | Source |
|---|---|---|
| `n_layer` (main blocks) | 65 | GGUF `qwen35.block_count` |
| attention layers | 16 | `qwen35.full_attention_interval = 4`, layer i is full-attention iff (i+1)%4==0 (`src/models/qwen35.cpp:21-28`, HEAD); recurrent layers = 65-16 = 48 |
| K bytes per token per attn layer | 576 B = 32 × 18 B | `qwen35.attention.head_count_kv = 4`, `attention.key_length = 256` (GGUF) → 1024 elements; `q4_0` block = 2 B (fp16 scale) + 16 B (qs) = 18 B per 32 elements (`ggml/src/ggml-common.h:194-199`, HEAD) → 1024/32 × 18 = 576 B; cache type from preset line 649 `cache-type-k = q4_0` |
| V bytes per token per attn layer | 576 B = 32 × 18 B | `attention.value_length = 256`, `head_count_kv = 4` → 1024 elements; same `q4_0` block (`ggml-common.h:194-199`); preset line 650 `cache-type-v = q4_0` |
| R bytes per recurrent layer (per cell) | 55,296 B | `n_embd_r = (ssm_d_conv-1)·(ssm_d_inner + 2·ssm_n_group·ssm_d_state)` = 3·(6144 + 2·16·128) = 3·8704 = 26,112 elements (`src/llama-hparams.cpp:183-204`, HEAD); stored as f32 (`src/llama-model.cpp:2400`, HEAD) |
| S bytes per recurrent layer (per cell) | 1,048,576 B | `n_embd_s = ssm_d_state·ssm_d_inner` = 128·6144 = 786,432 elements; f32 (`src/llama-model.cpp:2401`, HEAD) |
| Recurrent cells per slot | 1 | `state_write` writes one `cell_count` per layer, one cell per sequence (`src/llama-memory-recurrent.cpp:743-822`, HEAD); so the recurrent part is a constant, independent of `T` |
| Context | 32K / 64K / 100K tokens | production preset `ctx-size = 131072` (preset line 636); the 94-100K context of the task is bounded by it |

Size formula. The KV part scales with `T`; the recurrent part is a fixed per-slot constant (one
cell per layer, written once):

```
KV part      = n_attn · (k_row + v_row) = 16 · (576 + 576)              = 18,432 B per token
R_total      = n_recr · (r_row + s_row) = 48 · (55,296 + 1,048,576)   = 52,985,856 B   (constant)
File(T)      = 18,432·T  +  8·T  +  R_total  +  fixed
              = 18,440·T + 52,985,856 + 44
```

- The per-token KV term is `18,440 B = 18,432 (K+V) + 8` (4 B packed token payload + 4 B per-cell
  KV `pos`/`n_seq_id`, `src/llama-kv-cache.cpp:2053-2056`).
- `R_total = 48 · (55,296 + 1,048,576) = 48 · 1,103,872 = 52,985,856 B ≈ 50.5 MiB`, independent of `T`.
- Fixed bytes = 16 (file header magic+version+n_token_count) + 4 (KV `n_stream`) + 8 (per-stream KV
  `cell_count`, 2 streams) + 4 (recurrent `cell_count`) + 12 (`s_trans` + `n_layer`) = 44 B,
  written once per file.
- Hence `File(T) = 18,440·T + 52,985,856 + 44` bytes.

Hand check (T = 32,768):
- KV body: 18,432 × 32,768 = 603,979,776 B
- per-token meta: 8 × 32,768 = 262,144 B
- recurrent constant: 52,985,856 B
- fixed: 44 B
- total: 603,979,776 + 262,144 + 52,985,856 + 44 = 657,227,820 B ≈ 626.8 MiB ≈ 0.61 GiB

| Context | Estimated file size |
|---|---|
| 32,768 tokens | 657,227,820 B ≈ 626.8 MiB ≈ 0.61 GiB |
| 65,536 tokens | 1,261,469,740 B ≈ 1,203.0 MiB ≈ 1.17 GiB |
| 100,000 tokens | 1,896,985,900 B ≈ 1,809.1 MiB ≈ 1.77 GiB |
| 3 files (32K+64K+100K) | 3,815,683,460 B ≈ 3,638.9 MiB ≈ 3.55 GiB |

Disk check (measured 2026-10-13, `df -h /d`): D: total 895G, used 708G, **free 187G** — this
comfortably covers the three files (≈ 3.55 GiB total) with a large margin (~183 GiB to spare), so
the slot-save design is disk-viable on this drive.

### 9b. T010: hook restore into the watchdog, the apply scripts and the queue (done 2026-10-13)

Where the restore was hooked (all `.new` copies; the live originals were not touched):

- `docker_watchdog.ps1.new` — in `Restart-DockerDesktop`, after the engine answers and
  the containers are healthy (`Log 'containers are back'`), it waits for readiness by
  polling `Test-Url 'http://localhost:8033/health'` every 10 s for up to 5 minutes
  until it returns 200; only then it runs
  `python slot_cache.py --base-url http://localhost:8033 --save-dir D:\llama-slots restore`
  through the existing `Invoke-Limited` helper (120 s cap). The result is logged
  (`slot restored: …`, `slot restore: …` or
  `slot restore failed (ignored): …`); if `/health` never returns 200 it logs
  `slot restore skipped: llama-server not ready (ignored)`. A failure is never fatal —
  the watchdog keeps its 20 s probe loop.
- `apply_cms.new.sh`, `apply_llama_template.new.sh`, `apply_no_diag.new.sh` — these three
  restart/recreate `llama-cpp-server`; after the health check they run
  `python slot_cache.py --base-url http://localhost:8033 --save-dir /d/llama-slots restore >> real_queue.log 2>&1`
  (`apply_no_diag.new.sh` appends `|| true`). They do not recreate the server while a run
  is busy (their own polling), so the restore runs only when the server is up and healthy.
  The other apply scripts
  (`apply_display_fix.sh`, `apply_fix_now.sh`, `apply_runner_fix.sh`, `apply_maxrun.sh`,
  `apply_stepslimit.sh`, `apply_stream_fix.sh`, `apply_thinkswitch.sh`) only restart
  `claude-runner`/`litellm` or wait for a mission state: they do not restart llama-server,
  so no restore call is added.
- Queue integration: `slot_cache.py loop` runs **next to** `queue_watch.py` as a separate
  process (recommended) or is launched by the patched watcher
  (`queue_watch.py.new`, whose `start_queue()` starts
  `slot_cache.py loop --base-url http://localhost:8033 --save-dir D:\llama-slots` right
  before it relaunches `real_queue.sh`; it skips the start when a loop process is already
  running — the check lists processes whose command line matches `slot_cache.py loop`,
  where the pattern is built from two pieces (`$p='slot_cache'+'.py loop'`) so the check's
  own powershell process never matches itself).

#### Operator steps (apply)

1. Verify the live originals are unmodified:
   `git -C /d/n8n diff -- data-tables/ops/docker_watchdog.ps1 data-tables/ops/queue_watch.py data-tables/ops/real_queue.sh` must be empty.
2. Apply the watchdog:
   `copy D:\n8n\data-tables\ops\docker_watchdog.ps1 D:\n8n\data-tables\ops\docker_watchdog.ps1.bak-20261013T010`
   then `copy D:\n8n\data-tables\ops\docker_watchdog.ps1.new D:\n8n\data-tables\ops\docker_watchdog.ps1`.
   Start it as before:
   `powershell -NoProfile -ExecutionPolicy Bypass -File D:\n8n\data-tables\ops\docker_watchdog.ps1`
   (after every Docker Desktop restart it waits for readiness — polling
   `http://localhost:8033/health` every 10 s for up to 5 minutes until it returns 200 —
   and only then runs the restore with
   `--base-url http://localhost:8033 --save-dir D:\llama-slots`. The result appears in
   `docker_watchdog.log`; if `/health` never returns 200 the line
   `slot restore skipped: llama-server not ready (ignored)` is logged, and if
   `slot_cache.py` is missing or the restore fails the line
   `slot restore failed (ignored): …` or `slot restore: slot_cache.py not found (ignored)`
   is logged and the watchdog continues).
3. Apply the apply scripts (only if you run them again):
   `copy D:\n8n\data-tables\ops\apply_cms.sh D:\n8n\data-tables\ops\apply_cms.sh.bak-T010` (same for
   `apply_llama_template.sh` and `apply_no_diag.sh`), then replace each with its `.new` copy:
   `copy D:\n8n\data-tables\ops\apply_cms.new.sh D:\n8n\data-tables\ops\apply_cms.sh`, etc.
   Run them the usual way; each now runs, after the llama health check,
   `python slot_cache.py --base-url http://localhost:8033 --save-dir /d/llama-slots restore`
   and the output goes to `real_queue.log`.
4. Slot-save loop next to the queue — separate process (recommended):
   `powershell -NoProfile -Command "Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='\"C:\\Program Files\\Git\\usr\\bin\\python.exe\" /d/n8n/data-tables/ops/slot_cache.py loop --base-url http://localhost:8033 --save-dir D:\\llama-slots'}" | Select-Object -ExpandProperty ReturnValue`
   It saves slots during quiet periods (defaults: prompt unchanged 180 s, growth > 512 tokens,
   poll every 30 s; soft mode — every failure is logged and ignored, so it can never stop the queue).
   Its log is `D:\n8n\data-tables\ops\slotcache.log`; its state file is
   `D:\n8n\data-tables\ops\slot_cache_state.json`.
5. Or the patch: `copy D:\n8n\data-tables\ops\queue_watch.py D:\n8n\data-tables\ops\queue_watch.py.bak-T010`
   then `copy D:\n8n\data-tables\ops\queue_watch.py.new D:\n8n\data-tables\ops\queue_watch.py`,
   and restart the watcher (`python queue_watch.py` detached as before). From then on every
   queue relaunch by the watcher starts
   `slot_cache.py loop --base-url http://localhost:8033 --save-dir D:\llama-slots` right
   before relaunching `real_queue.sh`, skipping the start when a loop process is already
   running; the loop survives queue restarts without a separate process.
6. Save-dir (mandatory in every command above): the saved slot files live in
   `D:\llama-slots` — the directory bound to `/slots` inside the llama-server
   (`--slot-save-path`, section 9a). Every restore/loop command in steps 2–5 therefore
   carries `--save-dir D:\llama-slots` (`/d/llama-slots` in Git Bash in the apply
   scripts). Do not run the loop without this flag: the default save dir is the script's
   directory (`D:\n8n\data-tables\ops`), where the server cannot see the files.

#### Rollback

1. Watchdog: `copy D:\n8n\data-tables\ops\docker_watchdog.ps1.bak-20261013T010 D:\n8n\data-tables\ops\docker_watchdog.ps1`,
   then restart the watchdog process (the old `docker_watchdog.ps1.bak-20261013`/`-bak-20261013b`
   copies are from earlier and not the state before T010).
2. Apply scripts: restore the `.bak-T010` copies over the live names
   (`copy D:\n8n\data-tables\ops\apply_cms.sh.bak-T010 D:\n8n\data-tables\ops\apply_cms.sh`,
   same for the other two).
3. Queue watcher: `copy D:\n8n\data-tables\ops\queue_watch.py.bak-T010 D:\n8n\data-tables\ops\queue_watch.py`,
   restart the watcher; stop the slot loop with Ctrl-C (or
   `Get-Process python -ErrorAction SilentlyContinue | Where-Object CommandLine -match 'slot_cache.py' | Stop-Process -Force`).
4. Nothing else is affected: `real_queue.sh`, `queue_watch.py` and `docker_watchdog.ps1`
   were never modified in place, and `slot_cache.py` itself is unchanged.

Checks that were run: `diff` of the watchdog original vs `.new` (only the restore
block added after `Log 'containers are back'`), `bash -n` on the three `.new` apply
scripts and `py_compile` on `queue_watch.py.new` (all pass), `grep -n "slot_cache.py loop"`
on `queue_watch.py.new` (the literal appears only in the `start_queue()` command line and
in comments, not in the `slot_loop_running()` filter), and `git diff` on the three
live files (empty).

Backups first (identical to the originals, verified with `cmp`):
- `D:\OpenAIProjects\llama.cpp\docker-entrypoint.sh.bak-2026-10-13`
- `D:\OpenAIProjects\llama.cpp\docker-compose.yml.bak-2026-10-13`

Diff (the only intended lines, per `diff` against the .bak copies):

`docker-entrypoint.sh` — in the `[qwen3.8-27b-gsq-rco-iq2-s-mtp]` preset, after `checkpoint-min-step = 2048`
(lines 678–680):

```
# 2026-10-13: slot save/restore (POST /slots/{id}?action=save|restore) persists KV + recurrent state to
# /slots (bind volume D:/llama-slots); requires this flag or the server rejects the slot endpoints.
slot-save-path = /slots
```

`docker-compose.yml` — `llama-server` volumes, after the templates line (lines 28–29):

```
# slot save files (slot-save-path = /slots in the qwen3.8-27b preset)
- D:\llama-slots:/slots
```

The bind volume maps the host directory `D:\llama-slots` (create it on the D: drive before first
use) to `/slots` inside the llama-server container; only the `qwen3.8-27b-gsq-rco-iq2-s-mtp`
preset gets `slot-save-path` (the other manual sections are unchanged; the preset file is
regenerated from the script on every container start, so the flag takes effect only on the next
restart). No container was restarted or recreated in this session.

Rollback: restore from the .bak copies and remove the two lines above:

```
cp D:\OpenAIProjects\llama.cpp\docker-entrypoint.sh.bak-2026-10-13 D:\OpenAIProjects\llama.cpp\docker-entrypoint.sh
cp D:\OpenAIProjects\llama.cpp\docker-compose.yml.bak-2026-10-13 D:\OpenAIProjects\llama.cpp\docker-compose.yml
```

Verification that was run: `bash -n` on the entrypoint (pass), `diff` against the .bak copies
(only the intended lines differ), and a YAML parse of the compose file (the new volume is present).
`docker compose ... config --quiet` could not be run here (no docker CLI in this WSL environment);
the YAML parse is the stand-in for config validation.

**Operator step to verify the real size** (unverified until done):
1. Start the server with the production preset; `slot-save-path = /slots` is now in the
   `[qwen3.8-27b-gsq-rco-iq2-s-mtp]` preset (docker-entrypoint.sh:680) and maps to the
   `D:\llama-slots` bind volume (docker-compose.yml:29); the endpoint requires the flag
   (`tools/server/server-context.cpp:4645-4646`).
2. Run a prompt of known length N (e.g. 32K tokens) on a slot, then
   `curl -X POST "http://localhost:8033/slots/0?action=save"` (with the API key).
3. Record `n_tokens` and `n_bytes` from the response (`tools/server/server-context.cpp:2474-2476`)
   and `stat -c %s <file>` in the container; compare `n_bytes` with the formula above
   (`18,440·N + 52,985,856 + 44`). A consistent ratio confirms the f32 recurrent + q4_0 KV
   assumption; any deviation indicates a different `type_r`/`type_s` or row layout.
4. Since 187 GiB free covers ~3.6 GiB of slot files with margin, keep the plan of up to 3
   saved slots; if a real save measures larger (e.g. f16 weights on the recurrent tensors),
   re-estimate and reduce the number/length of saved slots accordingly.

## Part B: measurements

Read-only measurement script: `data-tables/ops/tool_output_stats.py` (parses the trace file
only, no network calls). The live trace `litellm/trace/cache_trace.jsonl` is still being
appended by the running LiteLLM instance, so the numbers below are taken from a frozen
snapshot, `docs/cold-prefill/partB-cache-trace-snapshot.jsonl` (1271 records, copied from the
live file at 2026-10-13 07:51 UTC). Command used:

```
python data-tables/ops/tool_output_stats.py trace --path docs/cold-prefill/partB-cache-trace-snapshot.jsonl
```

The acceptance command against the live file also exits 0:

```
python data-tables/ops/tool_output_stats.py trace --path litellm/trace/cache_trace.jsonl
```

### Trace fields (sampled with `head`)

First two records of the live file (`head`, full lines):

```
{"t": 1791802946.5, "conv": "2658cd9c27c9", "model": "qwen3.8-27b-gsq-rco-iq2-s-mtp", "n": 19, "chars": 200948, "tools": "4d0e3ada", "thinking_off": false, "first_diff": "new conversation"}
{"t": 1791803356.3, "conv": "2658cd9c27c9", "model": "qwen3.8-27b-gsq-rco-iq2-s-mtp", "n": 21, "chars": 215519, "tools": "4d0e3ada", "thinking_off": false, "prev_n": 19, "tools_changed": false, "first_diff": null, "shrunk": false}
```

A divergent record (3rd line, abridged — `old`/`new` are ~120-char context snippets):

```
{"t": 1791803410.8, "conv": "2658cd9c27c9", ..., "n": 23, "chars": 220083, "tools": "4d0e3ada", "thinking_off": false, "prev_n": 21, "tools_changed": false, "first_diff": 20, "kind_new": "user:text", "at_char": 12, "old": "...", "new": "...", "redo_chars": 4794, "redo_tokens_est": 1598, "from_end": 3}
```

Fields actually present in the snapshot (writer: `litellm/cache_trace.py:89-121`):

| Field set | Fields | Records in snapshot |
|---|---|---|
| all records | `t`, `conv`, `model`, `n`, `chars`, `tools`, `thinking_off`, `first_diff` | 1271 |
| not "new conversation" | + `prev_n`, `tools_changed`, `shrunk` | 1214 |
| `first_diff` is an int | + `at_char`, `kind_new`, `old`, `new`, `redo_chars`, `redo_tokens_est`, `from_end` | 37 |

`first_diff` values in the snapshot: `"new conversation"` = 57, `null` (pure extension) = 1177,
int index = 37. Single model throughout: `qwen3.8-27b-gsq-rco-iq2-s-mtp` (1271/1271).

### Questions the trace can answer

- Context size distribution per conversation and in aggregate (`chars`, `n`, bucket histogram).
- Cache divergences: where the request diverged from the previous one (`first_diff`, `kind_new`,
  `at_char`, `redo_chars`, `redo_tokens_est` = `redo_chars // 3`).
- Tool-list changes (`tools_changed`) and thinking switch-offs (`thinking_off`).
- Per-conversation request counts and growth over the trace window.

### Questions the trace cannot answer

- **Share of tool_result content**: `chars` is the serialized length of the whole message list
  (roles, JSON quotes, `reasoning_content` included); no per-message character count and no
  per-message role is stored, so tool results cannot be separated out. The trace allows only the
  aggregate composition above, not a tool-result share.
- True token counts: only the estimate `redo_tokens_est = redo_chars // 3` exists, and only for
  divergent requests.
- Per-slot cache hits inside llama.cpp: the trace compares consecutive requests of the same
  conversation, not the server's KV-cache state.

### Aggregate (snapshot)

| Metric | Value |
|---|---|
| requests | 1271 |
| conversations | 50 |
| total chars | 196,280,392 |
| chars/request: min / median / mean / max | 13,302 / 151,348.0 / 154,429.9 / 292,561 |
| messages/request: min / median / mean / max | 1 / 43.0 / 51.8 / 223 |
| first_diff: new conversation / pure extension / divergence | 57 / 1177 / 37 |
| total redo_chars (divergent requests) | 201,337 |
| total redo_tokens_est | 67,097 |
| tools_changed = true | 2 |
| thinking_off = true | 1048 |
| time span | 1791802946.5 – 1791878147.6 (2026-10-12 11:02:26 – 2026-10-13 07:55:47 UTC) |

### Chars distribution (snapshot)

| chars bucket | requests |
|---|---|
| 0–50k | 81 |
| 50–100k | 234 |
| 100–150k | 311 |
| 150–200k | 251 |
| 200–300k | 394 |
| 300k+ | 0 |

### Per-conversation stats (snapshot, sorted by total chars)

| conv | reqs | total chars | min chars | median chars | max chars | max n | divergences | redo_chars |
|---|---|---|---|---|---|---|---|---|
| a356c7e28cdb | 112 | 17,644,521 | 20,495 | 157,109.5 | 282,763 | 223 | 0 | 0 |
| 8177b2869e9e | 78 | 12,375,791 | 36,101 | 158,670.0 | 255,733 | 155 | 0 | 0 |
| 252d793335cb | 62 | 12,099,908 | 64,655 | 209,293.5 | 290,566 | 121 | 1 | 7,205 |
| 656f933e0008 | 53 | 9,524,133 | 46,245 | 173,192.0 | 280,881 | 101 | 3 | 7,943 |
| 70f70efd0742 | 74 | 8,640,983 | 13,302 | 111,336.0 | 230,941 | 147 | 1 | 7,205 |
| 149a1b337145 | 58 | 8,624,985 | 33,640 | 152,115.5 | 276,808 | 113 | 2 | 8,784 |
| ec3886b9fafc | 38 | 8,111,273 | 110,619 | 210,483.5 | 271,833 | 75 | 1 | 7,205 |
| e94e72ca97b9 | 38 | 7,997,772 | 60,098 | 240,349.0 | 288,750 | 73 | 2 | 7,459 |
| 98213ddcec55 | 46 | 7,723,792 | 34,576 | 168,653.5 | 288,568 | 93 | 0 | 0 |
| d8b52d9345e9 | 42 | 7,599,944 | 88,646 | 191,493.5 | 238,477 | 81 | 1 | 7,760 |
| 7b417b3ed22a | 39 | 7,589,136 | 59,204 | 195,567.0 | 265,994 | 77 | 1 | 7,205 |
| 3afbeaeec4fb | 66 | 7,033,222 | 15,003 | 116,430.5 | 162,455 | 133 | 0 | 0 |
| b8fa397f4886 | 34 | 6,919,430 | 52,813 | 221,721.5 | 264,954 | 67 | 1 | 7,205 |
| 699ef0cda726 | 40 | 6,799,555 | 59,677 | 186,760.5 | 292,561 | 79 | 1 | 7,159 |
| 865a99b0e649 | 41 | 6,580,832 | 51,040 | 171,386.0 | 252,095 | 81 | 1 | 7,205 |
| 82931e4ea890 | 30 | 5,767,555 | 20,988 | 215,456.5 | 262,443 | 61 | 0 | 0 |
| c6c0ac2ba619 | 45 | 5,654,144 | 45,660 | 137,303.0 | 187,685 | 91 | 0 | 0 |
| aaf3d64c33b2 | 24 | 4,768,244 | 62,768 | 208,039.5 | 287,343 | 47 | 1 | 7,205 |
| e1070ffabfc2 | 23 | 4,684,011 | 46,806 | 249,945.0 | 263,899 | 33 | 7 | 26,584 |
| 274d76ea5d43 | 27 | 4,389,537 | 52,476 | 163,647.0 | 243,704 | 53 | 1 | 7,205 |
| ee55a6b24f08 | 35 | 4,333,519 | 25,586 | 121,862.0 | 227,335 | 69 | 1 | 7,205 |
| a3a4ff050691 | 39 | 4,269,583 | 50,776 | 103,215.0 | 177,773 | 79 | 0 | 0 |
| abf24246bc46 | 36 | 3,454,575 | 21,424 | 106,460.5 | 133,387 | 73 | 0 | 0 |
| 5e8e6613d4fc | 15 | 3,418,518 | 127,706 | 251,594.0 | 271,889 | 21 | 6 | 28,574 |
| 65fe168b4aa5 | 25 | 3,405,476 | 56,358 | 140,583.0 | 209,405 | 49 | 1 | 7,205 |
| 95127886828f | 18 | 2,479,793 | 100,853 | 133,373.0 | 224,594 | 35 | 1 | 7,159 |
| d24a82aae506 | 14 | 2,313,576 | 95,758 | 153,378.5 | 247,547 | 27 | 1 | 7,205 |
| dbf3bd5eadbd | 21 | 2,163,540 | 49,493 | 108,899.0 | 134,338 | 43 | 0 | 0 |
| 62483165d570 | 9 | 2,143,174 | 229,277 | 238,921.0 | 244,789 | 51 | 0 | 0 |
| 2658cd9c27c9 | 9 | 1,989,670 | 200,948 | 223,402.0 | 229,481 | 35 | 1 | 4,794 |
| 016715bc72c7 | 9 | 1,669,590 | 166,612 | 187,652.0 | 200,656 | 71 | 1 | 7,205 |
| 0d801c624a05 | 16 | 1,032,895 | 52,741 | 66,573.5 | 73,378 | 31 | 0 | 0 |
| d39489d240f8 | 18 | 1,025,352 | 37,439 | 58,707.0 | 68,944 | 37 | 0 | 0 |
| 851bdc0f24ed | 9 | 606,975 | 45,514 | 71,817.0 | 80,348 | 19 | 0 | 0 |
| 9507e63fe557 | 7 | 437,937 | 59,001 | 62,623.0 | 65,708 | 15 | 0 | 0 |
| f5b1d111213f | 4 | 348,422 | 85,049 | 87,172.0 | 89,029 | 9 | 1 | 8,661 |
| d7bbc6eb6211 | 4 | 237,208 | 46,138 | 61,963.0 | 67,144 | 9 | 0 | 0 |
| 3acbf8cd113f | 1 | 35,411 | 35,411 | 35,411.0 | 35,411 | 1 | 0 | 0 |
| 2a43b78d7847 | 1 | 34,611 | 34,611 | 34,611.0 | 34,611 | 1 | 0 | 0 |
| 2d09386045ce | 1 | 34,523 | 34,523 | 34,523.0 | 34,523 | 1 | 0 | 0 |
| 9c7c13a2721f | 1 | 34,406 | 34,406 | 34,406.0 | 34,406 | 1 | 0 | 0 |
| c27463ab5d84 | 1 | 34,257 | 34,257 | 34,257.0 | 34,257 | 1 | 0 | 0 |
| f62f40e89eec | 1 | 34,191 | 34,191 | 34,191.0 | 34,191 | 1 | 0 | 0 |
| 69d5314b0585 | 1 | 34,156 | 34,156 | 34,156.0 | 34,156 | 1 | 0 | 0 |
| 2596c3e5e043 | 1 | 34,066 | 34,066 | 34,066.0 | 34,066 | 1 | 0 | 0 |
| 168d132fb2cf | 1 | 34,054 | 34,054 | 34,054.0 | 34,054 | 1 | 0 | 0 |
| 41b2a42c4a78 | 1 | 32,895 | 32,895 | 32,895.0 | 32,895 | 1 | 0 | 0 |
| f82a87ec155a | 1 | 31,499 | 31,499 | 31,499.0 | 31,499 | 1 | 0 | 0 |
| 4439b3db415c | 1 | 25,082 | 25,082 | 25,082.0 | 25,082 | 1 | 0 | 0 |
| 174e403242ca | 1 | 22,670 | 22,670 | 22,670.0 | 22,670 | 1 | 0 | 0 |

### Runner sqlite: tool_result share per tool

The trace snapshot above cannot separate tool_result content. The runner's own event
log can: every run's events are written to `/data/events/<run_id>.jsonl` in the
claude-runner container, each line a JSON event with `kind`, `id`, `text`. A
`tool_result` event carries the `tool_use_id` that matches the preceding `tool_use`
event's `id`, so each result can be attributed to its tool. The sqlite database
itself (`/data/runner.sqlite`) holds only the `runs` and `sessions` tables — no events
— so the measurement reads the event dump and joins it to `runs` for the role/model.

**Database location and schema** (read-only).

- Path in the container: `/data/runner.sqlite` (WAL mode; `runner.sqlite-shm` and
  `-wal` siblings). `CLAUDE_RUNNER_DATA_DIR=/data` (set in `claude-runner/Dockerfile:39`),
  and the store is created at `settings.data_dir / "runner.sqlite"`
  (`claude-runner/src/claude_runner/runs.py:97-99`, `store.py:20-61`).
- `runs` table, 27 columns: `run_id` (PK), `kind`, `session_id`, `claude_session_id`,
  `session_created`, `role`, `model`, `provider`, `cwd`, `input`, `status`, `output`,
  `error_code`, `error_message`, `http_code`, `cost_usd`, `num_turns`, `context_tokens`,
  `details` (JSON), `think_policy`, `think_from`, `think_after`, `think_after_steps`,
  `think_task`, `created_at`, `started_at`, `finished_at`.
- `sessions` table, 8 columns: `session_id` (PK), `claude_session_id`, `cwd`, `role`,
  `created_at`, `updated_at`, `last_run_id`, `context_tokens`.
- Events are NOT in the database; they live in `/data/events/<run_id>.jsonl`
  (`claude-runner/src/claude_runner/events.py:1`, `EventLog._path`).

In this snapshot the `runs` table holds 1,497 runs; 1,341 event files exist (older runs
roll over: 16 event files have no matching `runs` row and are counted as role/model "?").

**Export commands** (read-only, no restarts). The event dump and the sqlite dump were
copied into `docs/cold-prefill/`:

```
# Back up the read-only sqlite to a temp file inside the container (no writes to /data).
docker exec claude-runner python -c "import sqlite3; s=sqlite3.connect('file:/data/runner.sqlite?mode=ro',uri=True); d=sqlite3.connect('/tmp/runner.sqlite'); s.backup(d)"
# Copy that dump and the event log out of the container.
docker cp claude-runner:/tmp/runner.sqlite docs/cold-prefill/partB-runner.sqlite
docker cp claude-runner:/data/events/. docs/cold-prefill/partB-runner-events/
```

(The sqlite is opened read-only, `sqlite3` URI `file:...?mode=ro`, and only `SELECT`s are
run against it; the events are copied, never rewritten.)

**Measurement command** (exits 0 on the exported data):

```
python data-tables/ops/tool_output_stats.py runner \
    --events docs/cold-prefill/partB-runner-events \
    --runs docs/cold-prefill/partB-runner.sqlite
```

Chars are the true length of each result: the stored `text` length plus the
`[N more characters]` the event log appends when a result is clipped to 20,000
characters. Shares are computed over the true lengths; the per-tool column is the share
of all tool_result characters.

**Per tool (all roles/models combined), snapshot 2026-10-13 08:56 UTC:**

| tool | count | total chars | % of tool_result | median | p95 |
|---|---|---|---|---|---|
| Read | 6,806 | 35,546,344 | 62.917 | 2,824 | 20,559 |
| Bash | 15,278 | 19,074,891 | 33.763 | 524 | 5,289 |
| Grep | 1,521 | 1,367,947 | 2.421 | 333 | 3,727 |
| Edit | 2,135 | 275,727 | 0.488 | 102 | 187 |
| Glob | 180 | 148,327 | 0.263 | 133 | 6,228 |
| Write | 252 | 44,542 | 0.079 | 162 | 494 |
| Agent | 8 | 36,709 | 0.065 | 4,024 | 7,627 |
| Monitor | 6 | 1,811 | 0.003 | 302 | 302 |
| bash | 2 | 238 | 0.000 | 119 | 119 |
| ahawr-search | 2 | 152 | 0.000 | 76 | 76 |
| ToolSearch | 2 | 82 | 0.000 | 41 | 50 |
| ReportFindings | 3 | 61 | 0.000 | 21 | 21 |
| (total) | 26,195 | 56,496,831 | 100.000 | | |
| (stored) | | 53,651,766 | | | |

Total true (un-clipped) chars 56,496,831; stored (clipped) chars 53,651,766.

**Role/model split (top rows by true chars):**

| tool | role | model | count | total chars | % of tool_result | median | p95 |
|---|---|---|---|---|---|---|---|
| Read | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 4,419 | 26,490,087 | 46.888 | 3,183 | 23,043 |
| Bash | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 11,466 | 13,925,087 | 24.648 | 474 | 5,219 |
| Read | reviewer | opus | 809 | 3,396,388 | 6.012 | 2,454 | 12,530 |
| Read | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 796 | 2,602,741 | 4.607 | 1,920 | 10,351 |
| Read | worker | haiku | 324 | 1,043,092 | 1.846 | 2,220 | 10,309 |
| Bash | reviewer | opus | 588 | 1,013,493 | 1.794 | 727 | 5,634 |
| Grep | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | 791 | 961,851 | 1.702 | 585 | 4,677 |
| Read | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | 205 | 844,093 | 1.494 | 2,263 | 14,456 |
| Bash | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 662 | 829,185 | 1.468 | 727 | 5,239 |
| Bash | worker | haiku | 757 | 807,784 | 1.430 | 285 | 4,825 |
| Bash | worker | opus | 315 | 696,084 | 1.232 | 933 | 8,917 |
| Bash | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | 635 | 651,286 | 1.153 | 290 | 5,107 |
| Read | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp-gpu96 | 91 | 449,098 | 0.795 | 2,877 | 14,456 |
| Bash | worker | sonnet | 221 | 415,602 | 0.736 | 480 | 10,039 |
| Grep | reviewer | qwen3.8-27b-gsq-rco-iq2-s-mtp | 662 | 370,488 | 0.656 | 104 | 2,212 |
| Bash | architect | qwen3.8-27b-gsq-rco-iq2-s-mtp | 269 | 273,146 | 0.483 | 727 | 3,359 |

**Top 20 largest single tool results (true chars).** Only a short input hint is shown;
no result content or secret values are printed. All 20 are `Read` results (the largest
single results are file reads, clipped to 20,000 stored chars each).

| # | tool | true chars | role | model | run_id | seq | input hint |
|---|---|---|---|---|---|---|---|
| 1 | Read | 61,749 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 29 | `arg.cpp` |
| 2 | Read | 60,332 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 31 | `arg.cpp` |
| 3 | Read | 59,132 | reviewer | opus | run_ffd224dd402248b9b0a3f1b76db602a8 | 33 | `arg.cpp` |
| 4 | Read | 51,233 | reviewer | opus | run_f81837dd96044f64a94399e102a4664c | 60 | `Hermes_Run_Manager_v5.json` |
| 5 | Read | 50,563 | worker | haiku | run_d9e745d90ac2404b8491e3296a58a294 | 22 | `pages.tsx` |
| 6 | Read | 48,979 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_49c6442c08934574a2327885be718e00 | 63 | `service.py` |
| 7 | Read | 48,979 | reviewer | opus | run_f81837dd96044f64a94399e102a4664c | 31 | `service.py` |
| 8 | Read | 47,365 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 32 | `llama-cpp-dcfr-research.patch` |
| 9 | Read | 44,659 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_b9643566b44147d3aa430d09460f8327 | 13 | `indexer.py` |
| 10 | Read | 44,499 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_77ac0c754b4d47bd9bdac9915dca9561 | 226 | `indexer.py` |
| 11 | Read | 44,489 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 36 | `llama-cpp-dcfr-research.patch` |
| 12 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 23 | `service.py` |
| 13 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 58 | `service.py` |
| 14 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 86 | `service.py` |
| 15 | Read | 44,249 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_216ec12e47ff4b82a31e289a8a48d064 | 114 | `service.py` |
| 16 | Read | 44,123 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_ee0278788512411abe3f0797439a17f7 | 8 | `service.py` |
| 17 | Read | 43,562 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp | run_2b2b6c118337408fb2e2bbb0611a6b80 | 21 | `indexer.py` |
| 18 | Read | 42,566 | reviewer | opus | run_440bba04be6c43a2a34813581f1d526b | 34 | `llama-cpp-dcfr-research.patch` |
| 19 | Read | 42,519 | worker | qwen3.8-27b-gsq-rco-iq3-xxs-mtp | run_003a0e2fb4d94d54b0ff834f9c120062 | 20 | `test_admin_routes.py` |
| 20 | Read | 42,519 | worker | qwen3.8-27b-gsq-rco-iq2-s-mtp-gpu96 | run_511c87933c9540dbb22a3b8634f7dfe9 | 17 | `test_admin_routes.py` |

**What the runner data answers (unlike the trace):** the share of context characters
taken by tool_result, broken down by tool and by role/model, with per-result median/p95
sizes and the largest single results. The per-tool percentages above add up to the stated
tool_result total (100.000%; the rounded column sums to 99.999).

## Part B: options (env vars that limit tool output, and per-role scoping)

### 1. Env vars that cap tool output, with status

Each is labelled **supported** (confirmed present in the installed CLI and/or official
docs), **unsupported** (no such variable), or **unverified** (seen only as a default,
not exercised in a run). The installed CLI is
`/usr/local/bin/claude` -> `/usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe`
(v2.1.286); every constant below was grepped out of that binary, and each doc URL is
openable.

| Var | Status | What it caps | Default / max | Where it is read |
|---|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` | supported | chars of Bash/PowerShell output read back into the command result | default 30000, hard cap 150000 | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `bashOutputMaxChars` (setting) | supported | same as above, as a settings value; **replaces** `BASH_MAX_OUTPUT_LENGTH` when set (env var is then ignored) | default 30000, clamps to 4000–128000 | binary `claude.exe`; docs: https://code.claude.com/docs/en/settings (Output limits) |
| `MAX_MCP_OUTPUT_TOKENS` | supported | tokens in MCP tool responses | default 25000; warns past 10,000 tokens | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` | supported | token limit for a `Read` of a file | default 25,000 tokens | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` | supported | max output tokens of a response (and the ceiling the compaction window is measured against) | varies by model; unknown IDs: default 32,000, cap 128,000 | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | supported | auto-compact window in tokens | 100,000–1,000,000, capped at the model's context window | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | supported | percentage (1–100) of the auto-compact window at which compaction triggers | default ~95% (cannot raise above default) | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_MAX_CONTEXT_TOKENS` | supported | overrides the context window Claude Code assumes for the active model | n/a (model-dependent) | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH` | supported | max chars of each MCP tool description / server instructions sent to the model | default 2048 | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |
| `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` | unverified | strips selected env vars from subprocesses spawned by the CLI | n/a | binary `claude.exe`; docs: https://code.claude.com/docs/en/env-vars |

Binary evidence (grepped from `claude.exe`):
- `BASH_MAX_OUTPUT_LENGTH` appears 8 times; `bashOutputMaxChars` 4 times. The settings
  text reads: *"How many characters of a successful Bash or PowerShell command's output
  Claude receives inline (default 30000; values clamp to 4000-128000). Output past this is
  saved to a file and Claude receives a short preview plus the path. When set, this also
  replaces BASH_MAX_OUTPUT_LENGTH, which on its own only sizes the read-back window."*
- The hard cap 150000 and the `4000-128000` clamp both appear in the binary next to the
  Bash output path.
- `MAX_MCP_OUTPUT_TOKENS` appears 5 times; the docs state default 25000 and that a
  successful text result longer than 50,000 characters from a tool without the
  `anthropic/maxResultSizeChars` annotation is saved to a file.
- `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` appears 4 times; default is 25,000 tokens
  for file reads.

Notes:
- `bashOutputMaxChars` and `BASH_MAX_OUTPUT_LENGTH` do the same job; the doc is explicit
  that setting the former makes Claude Code **ignore** the latter. The env var is the
  knob the runner can set per-provider; the settings value is not part of the provider
  block (see below).
- There is **no** env var named `BASH_MIN_OUTPUT_LENGTH` or a separate "MCP input"
  cap; `MAX_MCP_OUTPUT_TOKENS` is the only MCP output limit. (unsupported for anything
  beyond the variables above.)

### 2. How `claude_cli.py` scopes the env to the Worker on the local model

The env is built per-run in `child_env` (`claude-runner/src/claude_runner/claude_cli.py:186-203`):

1. Start from the container env minus every `CLAUDE_RUNNER_*` key
   (`claude_cli.py:191`).
2. If the run has a provider and that provider **isolates credentials**
   (`provider.isolates_credentials`, `claude-runner/src/claude_runner/config.py:206-208`),
   pop the Anthropic credential keys (`CREDENTIAL_VARS`, `config.py:137-142`) and the
   secondary-model keys (`SECONDARY_MODEL_VARS`, `claude_cli.py:178-183`), then default
   every secondary model name to the run's model (`claude_cli.py:196-199`).
3. Overlay the provider's own env on top (`claude_cli.py:200`). This is the per-provider
   block: any `CLAUDE_RUNNER_PROVIDER_<NAME>__<VAR>` value becomes a key in
   `provider.env` (parsed in `config.py:291-320`, runner-only keys excluded at
   `config.py:306`), and is merged onto the child env.
4. Always add `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_RESUME_INTERRUPTED_TURN=1`
   (`claude_cli.py:201-202`).

The env is handed to the CLI verbatim at
`runs.py:318` (`env = child_env(provider, model)`) and passed to the subprocess at
`runs.py:343-346` (`asyncio.create_subprocess_exec(..., env=env, ...)`).

So the scoping works like this:
- A **local** provider (the local model behind LiteLLM) sets its own
  `ANTHROPIC_BASE_URL` (and therefore `isolates_credentials` is true), so it gets its own
  env block.
- Setting e.g. `CLAUDE_RUNNER_PROVIDER_LLAMA_LOCAL__BASH_MAX_OUTPUT_LENGTH=12000`
  puts `BASH_MAX_OUTPUT_LENGTH` **only on that provider's child env**
  (`config.py:291-306` -> `provider.env` -> `child_env` overlay at `claude_cli.py:200`).
  The Anthropic roles (Architect / Reviewer / generic on the cloud subscription) do not
  carry that provider block, so they keep the built-in default (30000).
- That is the mechanism to scope `BASH_MAX_OUTPUT_LENGTH` (and the other caps above) to
  **the Worker on the local model only**: the Worker run's provider is the local one, and
  the value lives in that provider's block, not in the container env.

`isolates_credentials` is the gate: it is true exactly when the provider block sets any of
`CREDENTIAL_VARS` (`config.py:206-208`). The local provider does; the Anthropic cloud
provider (which inherits the container's `ANTHROPIC_API_KEY`) does not, so the local
provider is the only one whose block can carry the extra caps.

### 3. Proposed thresholds, with estimated context savings from the T004 numbers

T004 numbers (from the snapshot above, "Runner sqlite: tool_result share per tool",
true chars): total tool_result = 56,496,831 chars; Read 35,546,344 (62.9%); Bash
19,074,891 (33.8%); Grep 1,367,947 (2.4%); the per-tool medians/p95s and the largest
single results (Read up to 61,749 true chars) are the relevant tails.

The caps act on **the read-back window**, i.e. how many characters of a tool result are
carried inline into the context before the rest is moved to a file. The savings
below are **measured, not estimated**: for each cap I counted the tool results whose
true size (stored chars + the `[N more characters]` clip remainder, the same read-only
computation T004 used) exceeds the cap, and took the sum of those over-limit chars.
The one command (read-only, same clip logic as `tool_output_stats.py`):

```bash
python3 - <<'PY'
import json, re, glob
CLIP_RE = re.compile(r"\n… \[(\d+) more characters\]$")
tot={}; above={}; CAPS={"Read":48000,"Bash":12000}
for p in glob.glob("docs/cold-prefill/partB-runner-events/*.jsonl"):
    pend={}
    for ln in open(p, encoding="utf-8"):
        ln=ln.strip()
        if not ln: continue
        try: e=json.loads(ln)
        except: continue
        if e.get("kind")=="tool_use":
            pend[e["id"]]=e.get("name","")
        elif e.get("kind")=="tool_result":
            tool=pend.get(e["id"],"")
            t=e.get("text") or ""
            m=CLIP_RE.search(t)
            true=len(t)+(int(m.group(1)) if m else 0)
            d=tot.setdefault(tool,[0,0]); d[0]+=1; d[1]+=true
            for tn,cap in CAPS.items():
                if tool==tn and true>cap:
                    a=above.setdefault((tn,cap),[0,0]); a[0]+=1; a[1]+=true
TOT=56496831
for (tn,cap) in sorted(above):
    n,ch=above[(tn,cap)]
    print(tn, cap, n, ch, round(100*ch/tot[tn][1],3), round(100*ch/TOT,3), ch//3)
PY
```

Run against `docs/cold-prefill/partB-runner-events/` (the T004 snapshot), it produced:

| tool | cap (chars) | n over limit | chars over limit | % of that tool's total | % of 56,496,831 total | tokens (chars/3) |
|---|---|---|---|---|---|---|
| Bash | 12,000 | 18 | 307,130 | 1.610% of 19,074,891 | **0.544%** | 102,376 |
| Read | 48,000 | 7 | 380,967 | 1.072% of 35,546,344 | **0.674%** | 126,989 |
| **combined** | | 25 | **688,097** | | **1.218%** | **229,365** |

(Saved chars = over-limit chars; % of total = saved chars / 56,496,831; tokens ≈
saved chars / 3. The Bash row is 0.544% of the total, **not** 0.5% — and the
review's example, 3.5% of 19,074,891 ≈ 668k chars ≈ 1.2% of the total, would
correspond to a much lower cap than 12,000; at 12,000 the real tail is 1.610% of
Bash.)

| Proposed cap | Value | Why that value | Est. savings (measured, T004) |
|---|---|---|---|
| `BASH_MAX_OUTPUT_LENGTH` (local Worker) | **12,000** chars | Bash median 524, p95 5,289; only 18 of 15,278 Bash results exceed 12,000, so the common case is untouched and only the outliers are filed. | **307,130 chars** = 1.610% of Bash's 19,074,891 = **0.544%** of the total = **~102k tokens**. |
| `bashOutputMaxChars` (settings, local Worker) | **12,000** (or 16,000) | Same knob as the env var, expressed as a setting; the env var is the one the runner can scope per-provider. | Same as the Bash row: **307,130 chars / 0.544% / ~102k tokens**. |
| `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` (local Worker) | **16,000** tokens (~48,000 chars) | Read median 2,824 but Read is 62.9% of tool_result and the largest reads are 44–61k true chars; 7 reads exceed 48,000. | **380,967 chars** = 1.072% of Read's 35,546,344 = **0.674%** of the total = **~127k tokens**. |
| `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (local Worker) | **8,192** (the runner's `DEFAULT_MAX_OUTPUT_TOKENS`, `config.py:175`) | Per-response output cap; the runner already defaults to 8,192 for the local model so reports are short and resumable. | Not a tool-output cap; a cut reply is then resumed, so the context impact is small. |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW` (local Worker) | **65,536** (`AUTO_COMPACT_WINDOW`, `config.py:173`) | The local model's window is 65,536; the runner's `_autocompact_threshold` (`config.py:221-238`) computes the trigger as `window - min(max_output, 20000) - 13000`. | Sets the compaction trigger, not a tool cap; combined with the tool caps it keeps each turn's context inside the window. |

Combined estimate (Worker, local model): the two tool caps together move
**688,097 chars ≈ 229k tokens (1.218% of the 56,496,831-char tool_result total)** out of
the context per snapshot — roughly 3.5% of a full 65,536-token window. The cost is that
over-limit results are re-read with a short `Read` using offset/limit (the prompt already
encourages this). Note: an over-limit `Read` is reported as an **error requiring
offset/limit, not silently saved to a file** (unverified — the runner's event log only
records the stored + clipped text, not whether Claude Code wrote the tail out); the same
applies to an over-limit `Read` on the Anthropic roles.

### 4. Part B implementation (T011)

Chosen values, implemented as `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS` (opt-in):
- `BASH_MAX_OUTPUT_LENGTH` = **12,000** chars (the Bash row above: 0.544% of the tool_result
  total, ~102k tokens).
- `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS` = **16,000** tokens (~48,000 chars; the Read row
  above: 0.674%, ~127k tokens).

Wiring:
- `Settings.local_tool_output_limits` (`config.py`) reads `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS`
  (default off, so with default settings the env Claude Code receives is unchanged for every
  role — the runner adds nothing at all until the flag is set).
- `child_env` (`claude_cli.py`) gets `role=` and `local_tool_output_limits=` and adds the two
  caps only when `role == "worker"` and the run's provider is not None (i.e. the Worker on
  the local model behind LiteLLM). `runs.py:318` passes both from the settings.
- `docker-compose.yml` passes `CLAUDE_RUNNER_LOCAL_TOOL_OUTPUT_LIMITS=${AHAWR_LOCAL_TOOL_OUTPUT_LIMITS:-0}`
  (safe default: off), and `.env.example` documents it; `.env` is not touched.

Expected savings (measured against the T004 snapshot, section 3): the two caps together move
**688,097 chars ≈ 229,000 tokens ≈ 1.218%** of the 56,496,831-char tool_result total out of
the context per snapshot (Bash 307,130 chars + Read 380,967 chars; ≈3.5% of a full 65,536-token
window).

Rollback: set `AHAWR_LOCAL_TOOL_OUTPUT_LIMITS=0` (or unset it) — the flag is read once per run
and no other behavior changes; no code change needed.

Rebuild: the change lives in the claude-runner image, so `docker compose up -d --build`
rebuilds the runner before the flag takes effect (the `.env`-only toggle works after that
rebuild; without rebuilding, the variable is ignored).

Tests: `test_config_cli.py::test_local_tool_output_limits_apply_only_to_the_local_worker`
(unit: the caps appear only for the Worker on the local provider and never for the Reviewer/
Architect, enabled or not) and `test_runs.py::test_local_tool_output_limits_env_reaches_only_the_local_worker`
(runner-level: the Worker's CLI process env carries both caps; the Reviewer on the local
provider and the Architect on Anthropic do not).

### 5. Worker prompt rules (T012, data-tables/agent_prompts.csv)

Changed rules, verbatim from `data-tables/agent_prompts.csv` (worker,1,true, lines 113-115):

> Do not read large files (over ~400 lines), logs or command outputs whole.
> Do not print whole diffs or logs: use `grep -n ... | head -50`, `head`, `tail`, or
> `--stat` for file size.
> Cap command output: quote at most ~30 lines of any command output in the report; for
> the rest give file:line references and the number of matches (`grep -c`).

Reviewer/Architect prompts are unchanged (neutral).

search_hook.py is deliberately unchanged: T004 shows `ahawr-search` results are 2 results
and 152 chars of 56,496,831 (line 662), a 0.000% share, so no saving is available there.

Expected savings (the env-var caps, section 3): the two caps together move
**688,097 chars ≈ 229,000 tokens ≈ 1.218%** of the 56,496,831-char tool_result total out of
the context per snapshot (Bash 307,130 chars + Read 380,967 chars; ≈3.5% of a full 65,536-token
window). The prompt rules above make the model re-read with offset/limit instead of
pasting the tail, so the caps and the rules act together.

### 6. Risks for Reviewer / Architect on Anthropic

- **Reviewer (Anthropic, `opus`)**: Reviewer is read-only and already runs with a smaller
  Read p95 (2,454 median; 12,530 p95). Lowering its tool-output caps is **low risk**
  because it does not edit or run build commands, and its largest single reads
  (`arg.cpp` 61,749 true chars) are exactly the ones a 16k-token Read cap would flag.
  An over-limit `Read` is reported as an **error requiring offset/limit, not silently
  saved to a file** (unverified — the event log does not record whether Claude Code
  writes the tail out). Risk: if the cap is too low, the Reviewer must re-Read with
  offset/limit to see the tail, adding turns; keep the Read cap >= ~16,000 tokens so the
  largest files stay inspectable.
- **Architect (Anthropic)**: Architect is decomposition-only (`dontAsk`, read-only) and
  rarely emits tool results (its Bash share is 0.483% of the snapshot). Capping its
  output is **low risk**, but its real context load is the mission text, not tool
  results. Do **not** lower its caps to save context; the mission text dominates.
- **Both on Anthropic**: the caps in section 2 are scoped to the **local** provider's env
  block (`config.py:206-208`, `claude_cli.py:200`), so they do **not** touch the Anthropic
  roles by default. If an operator *does* set a cap in the Anthropic provider block, the
  risk is that a long Read/Bash result gets filed and the model must re-read it — on a
  large window (Opus/Sonnet) that is tolerable, but it can add latency and turns.
- **General risk (all roles)**: capping tool output does not delete the data; it moves it
  to a file the model must re-open. If the cap is set below what the task needs (e.g.
  a 60k-char file that must be read whole for a diff), the model will hit the file and
  re-Read, which is fine but costs an extra turn. The safe floor is the p95 of the
  dominant tool (Read p95 ≈ 20,559 chars → ~6,900 tokens; Bash p95 ≈ 5,289 → ~1,800
  tokens), so caps at ~16,000 tokens (Read) and ~12,000 chars (Bash) keep p95 intact.

## Part C: shared prompt prefix between sessions (BEFORE numbers)

> Source: `data-tables/ops/prefix_stats.py` (run `python3 data-tables/ops/prefix_stats.py`).
> The request bodies are the `kind: prompt` events in `docs/cold-prefill/partB-runner-events/run_*.jsonl`
> (1,321 runs; roles: worker 660, reviewer 621, architect 25), joined to roles via
> `docs/cold-prefill/partB-runner.sqlite` (table `runs`, `run_id LIKE 'run_%'`).
> `litellm/trace/cache_trace.jsonl` contains only diff fields (no request bodies), so it is
> used only for context sizes and `first_diff` offsets, not for prefix measurement.
> Token estimate: chars // 3 (the same ratio used throughout this document; no tokenizer is
> called).

### 0. Request-body capture route (read-only / offline)

The request bodies that prefix_stats.py measures are the `kind: prompt` events written
by the claude-runner event dump (`docs/cold-prefill/partB-runner-events/run_*.jsonl`).
Each file is one run's stream-json; the first `kind: prompt` event is the full request
body the runner sends to the Claude Code CLI (user message only — the CLI's system
prompt and tool schemas are not in the body; see the estimate below).

Currently, neither LiteLLM nor the LiteLLM hooks log request bodies:
- `litellm/Dockerfile` line 20: `CMD ["litellm", "--config", "/app/config.yaml", "--host", "0.0.0.0", "--port", "4000"]` — no
  `--log-requests` / `--verbose` / `--debug api-request` flag is passed.
- `litellm/config.yaml`: no `request_logging` or `log_requests` setting; the only
  callbacks are `ahawr_hooks.proxy_handler_instance` and `think_leak_guard` (line 33).
- `litellm/ahawr_hooks.py` (MidSystemMessageHook, lines 139–170) moves mid-conversation
  system messages into user turns and caps compaction `max_tokens`; it does not write
  request bodies to any log. The only file it can trigger is `cache_trace.jsonl`
  (via `AHAWR_CACHE_TRACE=1`, line 165–169), and `cache_trace.py:78–122` records only
  the index of the first differing message and a ≤120-char window around it — not the
  full body.
- No log files exist under `litellm/` other than `trace/cache_trace.jsonl`.

To capture one Worker, one Reviewer, and one compact request body via a read-only
offline route, the operator would need to enable LiteLLM request logging (e.g. add
`--log-requests` to the LiteLLM CMD, or set `litellm_settings.log_requests: true` in
config.yaml) and then inspect the generated log. This has not been done offline; the
bodies used here come from the runner event dump, which is already present and read-only.

### 1. Divergence points

Each point is where two requests of the same role first differ. Sources are the workflow
node that builds the block and the prompt file that carries it.

| # | Divergence point | What differs | Source |
|---|---|---|---|
| 1 | Role identity sentence (char 9) | Worker: `You are a local coding Worker.` / Reviewer: `You are an independent technical Reviewer and acceptance judge.` | `data-tables/agent_prompts.csv` (enabled prompt per role); built into the prompt by **Prompt Templates** (line 537) from **Load Prompts** (line 523) |
| 2 | Working directory block (char ~4,818 for Worker) | The directory path and its MSYS shell form differ per mission | **Mission** node (line 54, jsCode line 45): `workingDirectoryBlock` = `WORKING DIRECTORY: <dir>` (+ ` (shell: <shell>)` when different) + "All project files are in this folder. Resolve SCOPE entries and every other relative path against it…" |
| 3 | TASK <id>: <title> (char ~4,860 for Worker) | Task id and title differ per task | **Build Worker Run Input** (line 3974): `TASK ${task.id}: ${task.title}` |
| 4 | OBJECTIVE (after TASK line) | Task objective text | **Build Worker Run Input** (line 3974): `OBJECTIVE:` block |
| 5 | ACCEPTANCE CRITERIA (after OBJECTIVE) | Numbered list per task | **Build Worker Run Input** (line 3974): `ACCEPTANCE CRITERIA:` block |
| 6 | RETRIEVED CONTEXT block (char ~6,551 for Worker) | `=== RETRIEVED CONTEXT profile=worker request=<id> chunks=<n> ===` — the request id and chunk count differ per run | **Retrieve Worker Context** (line 3829): retrieves per-task RAG context; the request id and chunk count are run-specific |
| 7 | Reviewer: ORIGINAL GOAL (char ~879) | Mission name and objective | **Build Reviewer Run Input** (line 3988): `ORIGINAL GOAL:` block from `Mission.original_goal` |
| 8 | Reviewer: CURRENT TASK (after ORIGINAL GOAL) | Task title | **Build Reviewer Run Input** (line 3988): `CURRENT TASK:` block |
| 9 | Reviewer: PREVIOUS REVIEW block (retry only) | Score and feedback text differ per attempt | **Build Reviewer Run Input** (line 3988): `PREVIOUS REVIEW OF THIS TASK (score <n>):` block, present only on retry |
| 10 | Reviewer: WORKER RESULT (after SCOPE) | Worker's report text | **Build Reviewer Run Input** (line 3988): `WORKER RESULT:` block from `Load Worker Result` |
| 11 | Worker: WORKER RESULT (retry only) | Previous report text (capped 16,000 chars) | **Build Worker Run Input** (line 3974): `YOUR PREVIOUS REPORT FOR THIS TASK (attempt <n>):` block, present only on review retry |
| 12 | Worker: FRESH SESSION RETRY (compact) | Digest text and task id differ per session | **Build Worker Run Input** (line 3974): `retryInput` variant, built when `retry_fresh_session` flag is set |

Note on tool descriptions: the runner event dumps are the prompt bodies sent to the Claude
Code CLI; tool descriptions are not in the prompt body but are part of the CLI's system
prompt (built by the CLI from its tool definitions). The `--tools` flag (set per role in
`claude-runner/src/claude_runner/claude_cli.py:136-138`) changes the tool set per role,
which changes the system prompt, but the system prompt is not in the request body logged
here. The `ahawr_hooks.py` callback (litellm/ahawr_hooks.py) moves mid-conversation
system messages into user turns but does not alter the first request's prefix.

### 3. CLI system-prompt + tools estimate (per role)

The request body logged in the event dump is the user message only. The Claude Code CLI
builds its system prompt from the role's system-prompt text (the role identity sentence
in `data-tables/agent_prompts.csv`, appended via `--append-system-prompt` if set) plus
the tool definitions. The `--tools` flag changes the tool set per role:
- `claude_cli.py:136–138`: `# A smaller tool set shrinks the system prompt (~15k → ~4k
  tokens), which matters for local models with small context windows.`
- `config.py:521–522`: `tools_for(profile, provider)` returns
  `profile.tools or (provider.tools if provider else "") or self.tools`.
- `config.py:476`: `tools=env.get("CLAUDE_RUNNER_TOOLS", "").strip()` (global default).
- `config.py:307`: provider `tools=block.get("TOOLS", "").strip()` (per-provider default).

Per-role tool configuration (from `config.py:324–331`):
- architect: `dontAsk`, disallowed `_READ_ONLY_DENY + _SECRET_DENY` — no `tools` set,
  inherits from provider or global.
- worker: `bypassPermissions`, disallowed `_SECRET_DENY + _GIT_DENY` — no `tools` set,
  inherits from provider or global.
- reviewer: `dontAsk`, allowed `_REVIEWER_BASH_ALLOW`, disallowed `_READ_ONLY_DENY +
  _SECRET_DENY + _REVIEWER_BASH_DENY` — no `tools` set, inherits from provider or global.

**Estimate** (marked, not measured from the CLI binary): the full tool set (all
built-in Claude Code tools) gives a system prompt of roughly 15,000 tokens; a reduced
tool set (the `--tools` flag with a subset) gives roughly 4,000 tokens. The exact
number depends on which tools are included and on the tool definitions' text. The
Worker–Reviewer shared prefix of 9 tokens covers only the user body ("You are a"); the
system prompt and tools are not shared between roles.

### 4. Date / run id / attempt-number inserts in the first 5,000 chars

A date in ISO form (`2026-09-28`) appears in the first 5,000 chars of 212 of the 1,321
prompts (all inside the task or retrieved-context text, before the `WORKING DIRECTORY:`
block at char ~4,818); it is not a template insert but task content. The string "run id"
appears in the first 5,000 chars of exactly 1 prompt (char 4,561, also task content).
An attempt-number pattern (`attempt <n>`) does not appear in the first 5,000 chars of any
prompt (0 hits). No date, run id, or attempt number is injected as a template field in
the prefix proper; all occurrences are task-specific text.

### 5. BEFORE shared prefix measurements

Measured as the number of leading characters identical between two prompts.
Pair count is the number of unordered pairs in the category.

| Pair category | pairs | min chars (min tok) | max chars (max tok) | mean chars (mean tok) |
|---|---:|---:|---:|---:|
| Worker–Worker (full input) | 217,470 | 0 (0) | 250,090 (83,363) | 3,022 (1,007) |
| Reviewer–Reviewer (full input) | 192,510 | 0 (0) | 19,371 (6,457) | 2,342 (780) |
| Worker–Reviewer (same working directory) | 41,554 | 9 (3) | 9 (3) | 9 (3) |
| Worker–compact (fresh-session-retry) | 45 | 5,031 (1,677) | 29,147 (9,715) | 5,607 (1,869) |
| Worker–resume (`RESUME TASK`) | 703 | 14 (4) | 948 (316) | 48 (16) |
| Reviewer–resume (`The previous attempt`) | 120 | 65 (21) | 335 (111) | 199 (66) |

Notes:
- Worker–Worker: the large spread (0 to 250,090) is because different tasks have different
  objectives, scopes, and retrieved context. The `--same-task` flag (run
  `python3 data-tables/ops/prefix_stats.py --same-task`) groups worker full-input prompts
  by (task id, working directory) and prints the intra-group shared prefix. Results:
  138 groups with ≥2 prompts, 1,336 intra-group pairs, min 1,190 chars (396 tok),
  median 4,956 chars (1,652 tok), max 7,948 chars (2,649 tok), mean 3,982 chars
  (1,327 tok). The distribution is: <3,000: 529, 3,000–6,000: 441, 6,000–7,500: 359,
  7,500–8,000: 7. Pairs that share ~6,000–7,500 chars diverge at the RETRIEVED CONTEXT
  request id (the `=== RETRIEVED CONTEXT profile=worker request=<id> chunks=<n> ===`
  line). Pairs that share ~1,190–1,573 chars diverge inside the worker prompt itself:
  the older `grep -n` SEARCH section vs the newer `ahawr-search` section (the SEARCH
  section was rewritten on 2026-09-30; see `data-tables/agent_prompts.csv`). The
  1,603-char outlier pair (run_9dbf5f8f… vs run_0fea863b… / run_e425b9fb… /
  run_3ba023a6…) is the same prompt version change at a slightly different offset.
- Worker–Reviewer: exactly 9 chars ("You are a") shared, diverging at the role identity
  sentence. Even with the same working directory and mission, the prompts share nothing
  beyond that first line.
- Worker–compact: the fresh-session-retry prompts share the retry preamble and the
  task/acceptance-criteria structure; they diverge at the digest content and task id.
- Worker–resume: the resume prompts share only the `RESUME TASK <id>` header (14 chars
  for same-id tasks) or less for different tasks.
- Reviewer–resume: the resume prompts share the error message and the "Continue the
  existing Reviewer task…" instruction; they diverge at the specific error text.

### 6. agent_prompts.csv neutralization check (2026-10-14)

The matching text normalization for `data-tables/agent_prompts.csv` was evaluated for the
working-directory block that the Worker and Reviewer role prompts carry (cell 3, the
system prompt column; both rows are byte-identical in that block). The block reads:

```
WORKING DIRECTORY IN BASH:

If you are running inside Git Bash / MSYS on Windows, backslash paths will
be corrupted inside double quotes. `cd "D:\..."` becomes `cd "D:..."` and
creates a folder named "DOpenAIProjectsai-chemistry-video".

ALWAYS use the MSYS form of the path: /d/OpenAIProjects/ai-chemistry-video

If the task gives a Windows path like D:\OpenAIProjects\ai-chemistry-video,
you MUST convert it to /d/OpenAIProjects/ai-chemistry-video before any `cd`,
`mkdir`, `touch`, `cat`, `ls`, or any other command.
```

Checked and found **not neutral**:
- The mission-specific MSYS path `/d/OpenAIProjects/ai-chemistry-video` (lines 8, 11 of the
  block) and the Windows form `D:\OpenAIProjects\ai-chemistry-video` are mission-specific.
  A neutral edit would replace them with a generic placeholder (e.g. `<working directory>`
  and the shell form), but the workflow does not template the CSV text — it copies the
  enabled prompt verbatim from `Load Prompts` into `Prompt Templates` and passes it
  straight to the role input. The working directory itself is added by the **Mission**
  node (`working_directory_block`, built from `missions.working_directory`) as a separate
  block at the top of the first user message. Removing the concrete paths from the CSV
  would therefore change the instruction's behavior (the model would no longer know which
  path to use), which is not neutral.
- The generic explanation of the MSYS/`cd` corruption ("backslash paths will be corrupted
  inside double quotes…") is neutral text and is left as is.
- The `WORKING DIRECTORY IN BASH:` heading and the "If the task gives a Windows path…"
  instruction are generic; only the concrete path instances are mission-specific.

Decision: **no edit applied** to `data-tables/agent_prompts.csv`. The neutralization is
already achieved by the workflow patch (`stable_prefix_patch.py`), which moves the
mission-specific `working_directory_block` out of the head of the first user message and
puts it in the tail; the shared role-prompt text (the CSV content) is unchanged and is the
same across roles and runs. The concrete path in the CSV remains mission-specific by
design — it is the instruction the model must follow, not a shared prefix.

Backup: `data-tables/agent_prompts.csv.bak-20261013-1007` (unchanged). No new backup was
created because no edit was applied.

### 7. AFTER shared prefix measurements (T015, 2026-10-14)

> Source: `data-tables/ops/prefix_stats_after.py` (run
> `python3 data-tables/ops/prefix_stats_after.py`).
> The AFTER prompts are rebuilt offline by executing the jsCode of
> `Build Worker Run Input` and `Build Reviewer Run Input` in
> `AHAWR_v13_ClaudeCode.json` (patched by `data-tables/ops/stable_prefix_patch.py`)
> with the actual role prompts from `data-tables/agent_prompts.csv`.  The BEFORE
> prompts are the same prompts built from the jsCode in
> `AHAWR_v13_ClaudeCode.json.bak` (pre-patch: WD block at the head; no RUN ID /
> ATTEMPT / DATE lines) with RUN ID / ATTEMPT / DATE inserted right after the WD
> block, matching the pre-patch layout.  The date is fixed to 2026-10-14 in both
> (the script stubs `new Date()` to a constant).  No call to LiteLLM or
> llama-server.  Each pair is two **distinct** representative prompts (different
> run ids / dates / working directories / digests / errors as appropriate), so
> the measured shared prefix is the largest the patched layout allows, not a
> self-comparison.  Token estimate: chars // 3 (same ratio as the BEFORE section).
> Before running the script, the jsCode is extracted from the workflow file and
> executed in Node.js with a `$` shim; the script exits non-zero if a variable
> marker sits before the `TASK ` content in the AFTER layout.

#### 7.1 BEFORE vs AFTER per pair

The stable-prefix patch moves the variable data (working-directory block, RUN ID,
ATTEMPT, DATE) from the **head** of the first user message to its **tail**.  The
shared role-prompt text (the `agent_prompts.csv` content) and the task content
(TASK, OBJECTIVE, ACCEPTANCE CRITERIA, VERIFICATION, SCOPE, RETRIEVED CONTEXT)
are unchanged in position.  The effect is:

- **same-mission pairs** (same task, same WD): the shared prefix now extends
  through the full task content up to the RETRIEVED CONTEXT request id (Worker)
  or the variable tail (Reviewer), instead of stopping at the working-directory
  block at the head.
- **cross-mission pairs** (different WD): the shared prefix now extends through
  the full role prompt and task content before diverging at the working-directory
  block (now at the tail), instead of diverging at the working-directory block
  (at the head, char ~4,818).
- **Worker–Reviewer**: unchanged — the role identity sentence differs at char 9
  in both layouts, so the shared prefix is still 9 chars ("You are a").

| Pair category | BEFORE same (chars / tok) | AFTER (chars / tok) | BEFORE live (chars / tok) | Divergence point (AFTER) |
|---|---:|---:|---:|---|
| Worker–Worker, same mission | 3,813 / 1,271 | 4,325 / 1,441 | 3,982 / 1,327 (same-task mean) | RETRIEVED CONTEXT request id |
| Worker–Worker, cross-mission | 3,595 / 1,198 | 4,325 / 1,441 | 4,818 / 1,606 (measured pair) | WORKING DIRECTORY block (at tail) |
| Reviewer–Reviewer, same mission | 3,252 / 1,084 | 4,208 / 1,402 | 2,342 / 780 (mean, all pairs) | WORKING DIRECTORY block (at tail) |
| Worker–Reviewer (same WD) | 9 / 3 | 9 / 3 | 9 / 3 | role identity sentence (char 9) |
| Worker–compact, feedback differs | 558 / 186 | 558 / 186 | 5,607 / 1,869 (mean) | digest content |
| Worker–compact, same feedback | 558 / 186 | 558 / 186 | 5,607 / 1,869 (same mean) | digest content |
| Worker–resume (same task) | 653 / 217 | 666 / 222 | 48 / 16 (mean) | RUN ID / ATTEMPT / DATE (at tail) |
| Reviewer–resume | 65 / 21 | 65 / 21 | 199 / 66 (mean) | error text |

Notes:
- **Worker–Worker, same mission**: BEFORE same (both prompts rebuilt from the
  pre-patch templates) shares 3,813 chars, diverging at the WD block (at the
  head).  AFTER shares 4,325 chars, diverging at the RETRIEVED CONTEXT request
  id (the WD block is at the tail, after the shared task content).  The live
  BEFORE same-task mean was 3,982 chars; the like-for-like BEFORE same number
  (3,813) is close to it, confirming the representative task content is a
  fair proxy.  The structural change: the WD block no longer limits the shared
  prefix.
- **Worker–Worker, cross-mission**: BEFORE same shares 3,595 chars, diverging at
  the WD block (at the head).  AFTER shares 4,325 chars, diverging at the WD
  block (now at the tail).  The full role prompt and task content are now
  shared before the WD block.  The live BEFORE measured pair shared 4,818 chars
  (diverging at the WD block at the head); the like-for-like BEFORE same is
  smaller because the representative task is shorter, but the divergence point
  is the same.
- **Reviewer–Reviewer, same mission**: BEFORE same shares 3,252 chars,
  diverging at the WD block (at the head).  AFTER shares 4,208 chars, diverging
  at the WD block (at the tail).  The live BEFORE mean (2,342 chars) was
  dragged down by cross-mission pairs that diverged at the WD block at the
  head; the like-for-like BEFORE same (3,252) is for same-mission pairs only
  and is larger than the live mean.
- **Worker–Reviewer**: no change.  9 chars in both layouts; the role identity
  sentence differs at char 9.
- **Worker–compact (fresh-session-retry)**: no improvement.  The FRESH SESSION
  RETRY block carries no WD / RUN ID / ATTEMPT / DATE data in either layout, so
  the patched node did not change the compact text; BEFORE same and AFTER are
  identical (558 / 186, diverging at the digest content).  0 s saved.  The live
  BEFORE mean (5,607 chars) is much larger because the live digest and task
  content are longer; the like-for-like numbers use the representative (short)
  digest and task content.
- **Worker–resume (same task)**: BEFORE same shares 653 chars (the raw .bak
  resume text, with no RUN ID / ATTEMPT / DATE), diverging at the task title.
  AFTER shares 666 chars, diverging at the RUN ID / ATTEMPT / DATE tail.  The
  +13 chars (+5 tok) are the RUN ID / ATTEMPT / DATE lines added at the tail;
  ~0.0-0.0 s saved.  The live BEFORE mean was 48 chars because different run ids
  diverged immediately; the like-for-like BEFORE same (653) is for the same
  task, no inserted variable data.
- **Reviewer–resume**: unchanged (65 chars in both).  The resume prompt is
  built by the runner, not the workflow node; the only difference between the
  two representative prompts is the error text.

#### 7.2 Estimated prefill savings per new session

At the llama-server throughput of 220–260 tok/s, each shared token saved is
one token that does not need to be re-prefilled.  Using like-for-like numbers
(BEFORE same and AFTER, both built from the same representative prompts on the
same inputs):

- **Worker–Worker, same mission** (same task, same WD): shared prefix extends
  from 1,271 tok (BEFORE same) to 1,441 tok (AFTER): +170 tok ≈ **0.7–0.8 s**
  saved per new session.
- **Worker–Worker, cross-mission** (different WD): shared prefix extends from
  1,198 tok (BEFORE same) to 1,441 tok (AFTER): +243 tok ≈ **0.9–1.1 s** saved
  per new session.
- **Reviewer–Reviewer, same mission**: shared prefix extends from 1,084 tok
  (BEFORE same) to 1,402 tok (AFTER): +318 tok ≈ **1.2–1.4 s** saved per new
  session.
- **Worker–compact** (fresh-session-retry, feedback differs): no improvement
  (558 / 186 in both layouts); **0 s** saved per new session.
- **Worker–resume** (same task): +13 chars (+5 tok) ≈ **0.0–0.0 s** saved per
  new session.
- **Worker–Reviewer** (same WD): no improvement (9 / 3 in both layouts).
- **Reviewer–resume**: no improvement (65 / 21 in both layouts).

Cross-mission gain (Worker–Worker, different WD): ~+243 tok, ~0.9–1.1 s per
new session — the full role prompt and task content are now shared before the
WD block instead of only the role prompt before it.

These are **estimates from representative prompts**, not live measurements.
Live confirmation requires running the patched workflow and reading the
`cache_trace.jsonl` first-diff offsets (an operator step, marked unverified).

#### 7.3 Method and limits

- The AFTER prompts are **representative**: they use the actual role prompts
  from `agent_prompts.csv` and the actual patched template structure, but
  with short, fixed task content and fixed variable values.  The absolute
  shared-prefix numbers are therefore lower bounds on what a full-size task
  would share; the **position** of the divergence point (the structural
  finding) is exact.
- BEFORE same is built from the pre-patch .bak jsCode on the same representative
  inputs; BEFORE live is the separate measured column from the event dumps.
- The two are comparable in **structure** (where the divergence moves) but not
  directly in **absolute length**, because the representative task content is
  shorter than the live task content.
- The `AHAWR_STABLE_PREFIX` hook (flag default 0) moves Claude Code's
  mid-conversation variable reminders (total tokens, date, run id, attempt,
  working directory) to the tail of the first user message.  The workflow
  patch (`stable_prefix_patch.py`) moves the workflow-built variable data
  (working-directory block, RUN ID, ATTEMPT, DATE) to the tail of the first
  user message.  Together they place all variable data at the tail, so the
  shared prefix is everything before the variable tail.
- The role prompts in `agent_prompts.csv` are **not** neutralized
  (see §6); the mission-specific MSYS path in the Worker and Reviewer prompts
  remains.  This means the Worker and Reviewer role prompts are not identical
  to each other (they diverge at char 9, the identity sentence), and neither is
  shared across missions (the MSYS path is mission-specific).  The stable
  prefix is therefore limited to the text before the first mission-specific
  element.
- Live verification (unverified, operator step): run one Worker and one
  Reviewer on the same task in the patched workflow, enable
  `AHAWR_CACHE_TRACE=1`, and check that `cache_trace.jsonl` reports the
  first-diff offset at the variable tail (not at the working-directory block
  at the head).

# T016: final validation and operator instructions (2026-10-14)

## 1. Changed files (T001–T015, per `git status --short`)

Modified (tracked):
- `AHAWR_v13.json` — main workflow; control flags + stable-prefix patch applied
- `AHAWR_v13_ClaudeCode.json` — Claude Code workflow; same patches
- `AHAWR_Status_Bot.json` — status bot workflow (control commands)
- `claude-runner/src/claude_runner/{claude_cli.py, config.py, runs.py}` — env caps,
  thinking switch, stable prefix
- `claude-runner/tests/{conftest.py, fake_claude.py, test_config_cli.py,
  test_context_probe.py, test_runs.py}` — test updates
- `litellm/ahawr_hooks.py` — hooks incl. `AHAWR_CACHE_TRACE`, stable prefix,
  think-leak guard
- `litellm/runtime/local_thinking` — runtime thinking config
- `litellm/test_ahawr_hooks.py` — hook tests
- `retrieval-service/{Dockerfile, pyproject.toml, src/ahawr_retrieval/{config.py,
  service.py}, tests/{conftest.py, test_retrieval.py}}` — PG store wiring,
  parity check
- `scripts/{patch_main_control_flags.py, patch_status_bot_control.py}` — patch
  scripts
- `docker-compose.yml`, `.env.example`, `.gitignore`,
  `docs/mission-report-T013-ru.md` — infra/docs updates

Untracked new files:
- `docs/cold-prefill.md` and `docs/cold-prefill/` (Part A/B/C, snapshot)
- `litellm/runtime/local_thinking.auto`
- `retrieval-service/scripts/`, `retrieval-service/src/ahawr_retrieval/pg_store.py`,
  `retrieval-service/tests/parity_check.py`
- `AHAWR_v13_ClaudeCode.json.bak`

`data-tables/ops/*.new*` (apply scripts, watchdog, queue watcher) live outside
git and were copied, not committed.

## 2. Defaults vs opt-in

On by default (active without any env var / flag):
- `slot-save-path = /slots` in the `qwen3.8-27b-gsq-rco-iq2-s-mtp` preset
  (llama.cpp `docker-entrypoint.sh:684`); bind volume
  `D:\llama-slots:/slots` (llama.cpp `docker-compose.yml:29`)
- The stable-prefix workflow patch baked into the exported workflow JSON
  (`AHAWR_v13_ClaudeCode.json`, 3 patched nodes) — no runtime flag

Opt-in (must be set explicitly):
- `AHAWR_STABLE_PREFIX=1` — moves Claude Code's mid-conversation variable
  reminders (total tokens, date, run id, attempt, working directory) to the
  tail of the first user message
- `AHAWR_CACHE_TRACE=1` — appends one record per request to
  `litellm/trace/cache_trace.jsonl` (default off)
- Tool-output caps in `claude_cli.py` (env vars per role; unset by default)
- Slot save loop (`slot_cache.py loop`) runs only if started (separate
  process, or via the patched `queue_watch.py.new`); soft-fail — it never
  stops the queue
- The watchdog's post-restart slot restore runs only when the patched
  `docker_watchdog.ps1` is installed (step 3 of Part A §9b)

## 3. Operator steps

1. **Restart llama with the new preset.**
   `docker restart llama-cpp-server` (or the usual apply script). The preset
   now carries `slot-save-path = /slots`
   (`/d/OpenAIProjects/llama.cpp/docker-entrypoint.sh:684`) and the bind
   volume maps `D:\llama-slots` → `/slots`
   (`/d/OpenAIProjects/llama.cpp/docker-compose.yml:29`). Create
   `D:\llama-slots` on the host before first use.
2. **Rebuild and restart the runner.**
   In the `claude-runner` container: `pip install -e .` (or rebuild the
   image) and restart. This installs the env-cap and stable-prefix changes
   in `claude_cli.py` / `config.py` / `runs.py`.
3. **Apply the `.new` files** (Part A §9b, steps 1–6):
   - watchdog: back up, then copy `docker_watchdog.ps1.new` →
     `docker_watchdog.ps1`; start it as before. After every Docker Desktop
     restart it polls `http://localhost:8033/health` and runs the slot
     restore only when ready.
   - apply scripts: back up, then copy each of
     `apply_cms.new.sh`, `apply_llama_template.new.sh`, `apply_no_diag.new.sh`
     over the live names; run them the usual way.
   - queue watcher (optional): back up, copy `queue_watch.py.new` →
     `queue_watch.py`, restart the watcher — it starts
     `slot_cache.py loop --base-url http://localhost:8033 --save-dir
     D:\llama-slots` before each queue relaunch. Or run the loop as a
     separate process (step 4 of Part A §9b).
4. **Save a slot and measure its size.**
   Run a prompt of known length N (e.g. 32K tokens) on a slot, then
   `curl -X POST "http://localhost:8033/slots/0?action=save"` (API key).
   Record `n_tokens`, `n_bytes` from the response and
   `stat -c %s <file>` in the container; compare with the formula
   `18,440·N + 52,985,856 + 44` bytes (Part A §9). Free disk was 187 GiB
   (measured 2026-10-13), which covers ~3.55 GiB for three slot files with
   margin.
5. **Cold-start restore check — `prompt eval time` in the llama log.**
   a. Restart llama cold — the slot comes up empty.
   b. Run the restore: it happens automatically after every Docker Desktop
      restart when the patched `docker_watchdog.ps1` is installed (it polls
      `http://localhost:8033/health` and then restores, see `docker_watchdog.log`);
      or run it manually:
      `python3 data-tables/ops/slot_cache.py restore --base-url http://localhost:8033 --save-dir D:\llama-slots`
   c. Send the first prompt that shares the saved slot's prefix.
   d. Grep the llama log for the prefill time of that prompt and compare it
      with the ~6–7 min cold prefill of the full prefix:
      `docker logs llama-cpp-server | grep "prompt eval time"`
      A small `prompt eval time` (proportional to the divergent tail, not
      the full prefix) means the restore worked. A value near the full
      cold-prefill time means the restore did not help — either the prompt
      diverges before the restored prefix, or the restore failed
      (`slot restore failed (ignored): …` in `docker_watchdog.log`).
6. **Check `cache_trace` after a live run.**
   Set `AHAWR_CACHE_TRACE=1` in the LiteLLM env, run one Worker and one
   Reviewer on the same task, then inspect
   `litellm/trace/cache_trace.jsonl`: the `first_diff` offset should sit at
   the variable tail (RUN ID / ATTEMPT / DATE / WD block), not at the
   working-directory block at the head (Part C §7.3).

## 4. Rollback (per part)

- **Slot save/restore (llama.cpp):** restore the two backup files
  ```
  cp /d/OpenAIProjects/llama.cpp/docker-entrypoint.sh.bak-2026-10-13 \
      /d/OpenAIProjects/llama.cpp/docker-entrypoint.sh
  cp /d/OpenAIProjects/llama.cpp/docker-compose.yml.bak-2026-10-13 \
      /d/OpenAIProjects/llama.cpp/docker-compose.yml
  ```
  then restart llama. The slot endpoints are rejected again (no
  `--slot-save-path`); saved files in `D:\llama-slots` become inert.
- **Watchdog / apply scripts / queue watcher (Part A §9b):** restore the
  `.bak-T010` copies over the live names (watchdog, three apply scripts,
  `queue_watch.py`), restart the affected processes, stop any
  `slot_cache.py loop` process (Ctrl-C or
  `Get-Process python | Where-Object CommandLine -match 'slot_cache.py' |
  Stop-Process -Force`). `real_queue.sh`, `queue_watch.py`,
  `docker_watchdog.ps1` were never modified in place, and
  `slot_cache.py` itself is unchanged.
- **Stable-prefix workflow patch:** the pre-patch file exists for the
  Claude Code workflow: `AHAWR_v13_ClaudeCode.json.bak` (110 nodes, no
  `stable-prefix` markers — verified against the patched file, which has
  3). The patch script also ships a revert that copies the `.bak` back:
  `python3 data-tables/ops/stable_prefix_patch.py --revert AHAWR_v13_ClaudeCode.json.bak`
  (no git checkout). The main workflow `AHAWR_v13.json` is not touched by
  `stable_prefix_patch.py` (that script only patches
  `AHAWR_v13_ClaudeCode.json`), and its working-tree changes are only the
  control-flag patch — its pre-patch source is HEAD (`git show
  HEAD:AHAWR_v13.json`, commit 3508408, verified marker-free). The patch
  scripts are idempotent — re-running them on a patched file prints
  "already patched, skipping" / "already applied" and leaves the file
  byte-identical.
- **Runner env caps / thinking switch:** unset the relevant env vars in the
  `claude-runner` container and restart. No code rollback needed for the
  caps (they are opt-in env vars); the thinking switch default is off.
- **Cache trace:** set `AHAWR_CACHE_TRACE=0` (or unset) in the LiteLLM env
  and restart; the hook then stops appending. Existing
  `cache_trace.jsonl` lines remain.
- **agent_prompts.csv:** no edit was applied (Part C §6), so nothing to
  roll back.

## 5. Validation results (all commands run 2026-10-14, exit 0)

| Check | Command | Result |
|---|---|---|
| slot_cache tests | `python3 data-tables/ops/test_slot_cache.py` | 31 passed, OK |
| claude-runner (runner + search_hook) | `PYTHONPATH=/d/n8n/claude-runner/src /opt/claude-runner/venv/bin/python -m pytest claude-runner/tests/ --no-cov` | 143 passed |
| litellm hooks | `python3 -m pytest litellm/test_ahawr_hooks.py -q` | 19 passed |
| retrieval service | `cd retrieval-service && PYTHONPATH=src python3 -m pytest tests/test_retrieval.py -q --no-cov` | 57 passed |
| js syntax (workflow jsCode) | `node data-tables/ops/js_syntax_check.js AHAWR_Status_Bot.json AHAWR_v13.json AHAWR_v13_ClaudeCode.json` | 15/45/48 checked, 0 bad |
| workflow structure | `python3 scripts/check_workflows.py` | 3 files OK, no token/key |
| patch idempotency | `cp AHAWR_v13.json /tmp/t016_v13.json && python3 scripts/patch_main_control_flags.py /tmp/t016_v13.json && cmp AHAWR_v13.json /tmp/t016_v13.json` (same for ClaudeCode) | "already patched, skipping"; file byte-identical |
| bash syntax | `bash -n data-tables/ops/{apply_cms.new.sh, apply_llama_template.new.sh, apply_no_diag.new.sh, real_queue.sh}` | all exit 0 |
| CSV validity | `python3 -c "import csv; ..."` on `data-tables/agent_prompts.csv` | 4 rows × 5 cols parsed |
| JSON validity | `python3 -m json.tool` on the 3 workflow files; per-line `json.loads` on `litellm/trace/cache_trace.jsonl` | all OK (2958 lines) |
| compose config | `docker compose config --quiet` | **not run** — no docker CLI in this WSL environment; YAML parse of `docker-compose.yml` passed as the stand-in (services: ahawr-dashboard, ahawr-postgres, ahawr-retrieval, claude-runner, litellm, n8n) |
| forbidden files | `git status --short .env data/ data-tables/ops/real_queue.sh data-tables/ops/queue_watch.py data-tables/ops/docker_watchdog.ps1` | empty (unchanged) |
| no git mutations | `git log --oneline -5` | no new commits; HEAD still `3508408`; no stash/checkout/reset run |

Notes:
- The claude-runner suite must be run with the venv interpreter
  (`/opt/claude-runner/venv/bin/python`) and `PYTHONPATH=/d/n8n/claude-runner/src`:
  the system `python3` (3.12.15) has a stale, non-editable `claude_runner`
  install that shadows the working tree, which breaks
  `test_search_hook.py` (it spawns `sys.executable -m claude_runner.search_hook`).
  With the correct interpreter all 143 tests pass, including the search-hook
  exit-code test.
- `docker compose config --quiet` is the one check that could not be run
  here (no docker CLI). The YAML parse covers the same validation for the
  compose file in this repo; the llama.cpp compose file was validated by
  diff against its `.bak` (only the two slot lines added).

