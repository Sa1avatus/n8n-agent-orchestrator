# Part A: does `llama.cpp` slot save/restore carry KV, GDN recurrent state and checkpoints

Source tree analyzed: `D:/OpenAIProjects/llama.cpp` (working tree has `src/`, `tools/`, `common/` deleted;
all files quoted below were read from `git show HEAD:<path>` of that repo, so the line numbers are
`HEAD` line numbers).

## 1. What the slot save/restore/erase endpoints do

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

## 2. Slot save file format

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

## 3. What the state stream contains: KV and recurrent (GDN) state

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

## 4. Context checkpoints: not saved, not restored

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

## 5. Prefix matching after a restore

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

## 6. Verdict

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

## 7. Alternatives (since checkpoint persistence is missing)

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

## 8. Unverified (operator check)

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
