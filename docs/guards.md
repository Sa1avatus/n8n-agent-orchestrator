# Guards inventory

Every host watcher / guard and its place in the mission (MOVE / RUNNER / STAY ON THE HOST).
Intervals and check details are quoted from the scripts themselves, not from the notes.

## Table

| name | script / function | checks | interval | action | notice | runs where | verdict | reason | what the dashboard can show |
|---|---|---|---|---|---|---|---|---|---|
| **orphan_guard** | `orphan_guard.py` `main()` (112) | `claude -p` CLI processes in the container (`docker exec claude-runner ps`, line 69); runs from `GET http://localhost:8701/api/runs?limit=100` (header `X-AHAWR-Dashboard: 1`, line 84); a process is an orphan when no `running`/`queued` run carries its `session_id` / `claude_session_id`; counts `interrupted` runs closed < `QUICK_END_SECONDS` (120 s, line 35) after start | every `CHECK_SECONDS` = 30 s (env `ORPHAN_CHECK_SECONDS`, line 33) | kills a process seen as orphan in two consecutive checks and older than `MIN_AGE_SECONDS` = 120 s (env `ORPHAN_MIN_AGE`, line 34): `kill`, then `kill -9` 10 s later if still alive (lines 94–102); does nothing when docker or the API do not answer | kill → `"Страж раннера: остановлен процесс claude-сирота (pid …, возраст … мин, сессия …). Его запуск уже был закрыт, процесс занимал слот llama."` (line 144); 3 quick `interrupted` ends in 15 min → one "interrupt loop" notice (lines 158–162), no automatic action | host, `python orphan_guard.py` | **MOVE** | needs only the container's `ps` (docker exec) and the dashboard HTTP API; no Windows, no schtasks, no nvidia-smi | last heartbeat: the last `orphan_guard.log` line, or the count of kills in the log tail |
| **orphan_guard.log** | `log()` (40) | timestamped append of every check result | every check (30 s) | — | — | host, file `data-tables/ops/orphan_guard.log` | — (part of orphan_guard) | — | last heartbeat read from the file (mounted at `/d/n8n/data-tables/ops` in claude-runner) |
| **notify** | `notify.py` `send()` (55) | not periodic — called on demand by every other guard; builds the notice text, prepends the mission tag, POSTs to the n8n webhook | on demand | sends one Telegram message via the n8n webhook `http://localhost:5678/webhook/<path>` (line 70); returns True on 200 | the caller's text, optionally prefixed `"📌 <state_key> · Txxx"` (mission_tag, line 27) | host, `python notify.py "text"` or imported | **MOVE** | the webhook endpoint and the Postgres it queries are both Docker containers (n8n and `ahawr-postgres` per `docker-compose.yml`); nothing here needs Windows, schtasks or nvidia-smi — it is plain HTTP plus a Postgres query | last heartbeat: `notify.py` never writes a state file — the caller's log carries the sent/dropped markers |
| **queue_watch** | `queue_watch.py` `main()` (190) | new lines of `real_queue.log` (offset key `q`): `MISSION NOT COMPLETED`, `ended …: completed`, `REAL QUEUE STOPPED`, `ALERT:`, `=== real queue done` (lines 204–217); new lines of `docker_watchdog.log` (offset key `d`): `restarting Docker Desktop`, `containers are back`, `not restarting again` (lines 219–224); the queue bash process (`queue_alive`, line 122); n8n `AHAWR v13%` executions in `running`/`new`/`waiting` (`executions_running`, line 129); runner `running`+`queued` runs via `localhost:8700/health` (`runner_running`, line 138) | every 60 s (`time.sleep(60)`, line 262) | restarts the queue via `start_queue()` (line 182, PowerShell `Win32_Process Create` of `bash -c "…NOWAIT=1 real_queue.sh"`) when the process is gone, `queue_end != "done"`, `executions_running() == 0` and fewer than 4 restarts in the last hour (lines 232–239); idle notice after 1200 s (line 246) | `❌ Миссия … не завершена` + last review (`last_review`, line 100); `✅ Миссия … завершена.`; `⛔ Очередь … остановилась`; `⚠️ <alert>`; `🏁 Очередь … закончилась`; `🐳 Docker …` (three markers); `🔁 Очередь … запущена заново`; `💤 Ничего не выполняется уже 20 минут …` (line 247); capped at 20/hour, pending kept up to 6 h (`retry_pending`, line 55) | host, `python queue_watch.py` | **main() is split**: the notice and bookkeeping parts (log tails, state file, pending, cap, idle timer) are **MOVE**; the queue-alive check, the queue restart, the relay keep-alive and the think-archive trigger are **STAY ON THE HOST** (see the sub-rows) | last heartbeat: `queue_watch.state.json` offsets (file) or the last `queue_watch.log` line |
| **queue_watch: retry_pending** | `retry_pending()` (55) | resends pending notices with a late-event prefix; drops them after 6 h; stops on first failure | every tick (60 s, called from `main`) | resends via `notify.send` | — | host | **MOVE** | pure in-memory Python plus `notify.send`, which itself is MOVE (container webhook + Postgres) | pending count is in-memory, not a file — not directly dashboard-visible |
| **queue_watch: new_lines / read_from** | `new_lines()` (80), `read_from()` (92) | reads new log lines from the stored offset; first run starts at the end, a truncated file is re-read from the start | every tick (60 s) | updates offsets in `queue_watch.state.json` | — | host | **MOVE** | both files (`real_queue.log`, `docker_watchdog.log`) live in `data-tables/ops`, mounted at `/d/n8n/data-tables/ops` inside claude-runner; the state file is plain Python | `queue_watch.state.json` (file) is the last heartbeat |
| **queue_watch: idle notice** | the idle branch of `main()` (243–251) | while the queue is alive and `executions_running() == 0` and `runner_running() == 0`, a 1200 s idle timer runs | 1200 s | one `💤 Ничего не выполняется уже 20 минут …` notice (line 247) | the idle notice (line 247) | host | **MOVE** | the condition reads only the runner `/health` endpoint and the Postgres count, both Docker/HTTP; **as written it never fires when the queue is only queued**: `runner_running()` counts `running` + `queued` (line 144), so a queued-only queue keeps `runner_running() > 0`, the else branch resets `idle_since`/`idle_notified` every minute (lines 250–251) and the 20-minute timer never starts (gap `queue_watch.py:244–246`) — the container guard must count `running` only | last heartbeat: the last `queue_watch.log` line |
| **queue_watch: last_review** | `last_review()` (100) | newest `review_parsed` event of the mission from the `EVENTS` table | on notice (mission not completed) | appends the review text to the notice | — | host (Postgres via pgdb) | **MOVE** | a Postgres query against the `ahawr-postgres` container; no Windows dependency | not a heartbeat — a query at notice time |
| **queue_watch: queue_alive** | `queue_alive()` (122) | PowerShell `Get-CimInstance Win32_Process` count of `bash.exe` running `real_queue.sh` | every tick (60 s) | True when > 0 | — | host (PowerShell CIM) | **STAY ON THE HOST** | counts a host process with `Get-CimInstance`; the process being counted (`bash.exe` running `real_queue.sh`) is a host process, not a container process | — |
| **queue_watch: executions_running** | `executions_running()` (129) | Postgres join of `execution_entity` / `workflow_entity` for workflows `like 'AHAWR v13%'` in status `running`/`new`/`waiting` | every tick (60 s) | count (−1 when Postgres does not answer) | — | host (Postgres via pgdb) | **MOVE** | queries the `ahawr-postgres` container over pgdb; it is a Postgres query with no Windows, schtasks or nvidia-smi dependency | count visible via the n8n API, not a file |
| **queue_watch: log** | `log()` (37) | timestamped append of every check result | every tick (60 s) | — | — | host, file `data-tables/ops/queue_watch.log` | **MOVE** | a plain file write into the mounted ops folder | last heartbeat read from the file |
| **queue_watch: runner_running** | `runner_running()` (138) | `running` + `queued` runs from the runner health endpoint `localhost:8700/health` (Bearer `CLAUDE_RUNNER_API_KEY`, via `docker exec claude-runner curl`) | every tick (60 s) | count (−1 when unreachable) | — | host (docker exec → curl) | **MOVE** | pure HTTP against the runner health endpoint (the `docker exec` hop is only the transport); no Windows dependency | last heartbeat: the runner `/health` JSON, already readable from the dashboard |
| **queue_watch: slot_loop_running** | `slot_loop_running()` (149) | checks for a `slot_cache.py loop` process (PowerShell CIM count) | every tick (60 s) | — (defined but not called in this file) | — | host (PowerShell CIM) | **STAY ON THE HOST** | counts a host process with `Get-CimInstance`; the check itself is a Windows CIM call, whatever process it counts | not a heartbeat — not called in `main` |
| **queue_watch: archive_think_steps** | `archive_think_steps()` (161) | runs `think_archive.py` as a subprocess (timeout 900 s); the first call fires immediately on watcher start | at most once every 6 h (`LAST_ARCHIVE`, line 158; `time.time() - LAST_ARCHIVE[0] < 6 * 3600` guard, line 163) | appends not-yet-archived runs to `think_steps_archive.jsonl` | stdout/stderr tail (160 chars) written to `queue_watch.log` (line 167) | host | **MOVE** (the trigger, with the extract running inside claude-runner) | the extract script already runs inside the container (piped via `docker exec -i claude-runner python -` from the host, timeout 900 s) and the archive file is written into the mounted `/d/n8n/data-tables/ops` — from a dashboard trigger there is no Docker CLI and no Windows dependency; the host-side form stays only because the trigger currently sits in host `queue_watch.py` | last heartbeat: `think_steps_archive.jsonl` (file, visible in claude-runner at `/d/n8n/data-tables/ops`) |
| **queue_watch: ensure_relay** | `ensure_relay()` (170) | counts `python.exe` processes whose command line matches `tg_relay.py` (PowerShell CIM); if zero and `tg_relay.enabled` exists, starts it | every tick (60 s) | starts `D:\Python312\python.exe <ops>\tg_relay.py` via PowerShell `Win32_Process Create` and logs `tg_relay started again` | — | host (PowerShell CIM + process creation) | **moves to the container guard TG_RELAY (see Migration of the host watchers)** | the relay and its start check move to the container; no host process remains | not a heartbeat — the relay's own log (`tg_relay.log`) could be read |
| **queue_watch: start_queue** | `start_queue()` (182) | — | on restart decision (in `main`) | starts `real_queue.sh` via PowerShell `Win32_Process Create` (`NOWAIT=1`, output to `real_queue.out`) | `🔁 Очередь миссий запущена заново …` (line 238) | host (PowerShell process creation) | **STAY ON THE HOST** | starts a host bash process (`bash.exe` from Git for Windows); no in-container equivalent as written | not a heartbeat — `real_queue.out` / `real_queue.log` carry the queue's own log |
| **docker_watchdog** | `docker_watchdog.ps1` main loop (192–242) | `Test-Engine` (29): `docker version` with a 12 s hard limit; `Test-Url` (121): n8n `http://localhost:5678/healthz`, dashboard `http://localhost:8701/healthz`, llama `http://localhost:8033/health`; `Write-VmSample` (106): Docker VM memory via `wsl.exe -d docker-desktop` reading `/proc/meminfo` + `/proc/pressure/memory` + `/proc/pressure/io` (only when the engine answers) | every 20 s (`$everySeconds = 20`, line 22) | engine hung 4 probes in a row (`$hungAfter = 4`, line 23) → `Restart-DockerDesktop` (137): stop Docker Desktop processes, `wsl.exe --shutdown`, move the stale `docker-secrets-engine` folder, start Docker Desktop, wait up to 6 min for the engine and up to 6 min for ≥ 6 healthy containers, then `slot_cache.py restore`; dashboard down while engine answers 5 probes (`$dashAfter = 5`, line 24) → `docker restart ahawr-dashboard` (line 229); at most 3 restarts per hour (`$maxRestartsPerHour = 3`, line 25) → stops restarting, writes `real_queue.alert`, sleeps 300 s | VM under memory pressure (`avail_mb < 400` or `mem_full10 > 20`) once per 15 min → text line in `docker_watchdog.log` + Telegram via `notify.py` (lines 112–117); restart/stall/stop markers in `docker_watchdog.log` (`restarting Docker Desktop`, `containers are back`, `not restarting again`) | host, `powershell -NoProfile -ExecutionPolicy Bypass -File docker_watchdog.ps1` | **STAY ON THE HOST** | needs the Windows PowerShell process table (`Get-Process`, `Get-CimInstance`, `Get-Counter`), Docker Desktop control, `wsl.exe --shutdown` / `wsl.exe -d docker-desktop`, and `nvidia-smi` in the snapshot — all Windows/WSL host facilities | last heartbeat: `docker_watchdog.csv` (one row per 20 s probe) and `docker_vm.csv` (one row per engine-alive probe); both are files, readable via the mount |
| **docker_watchdog: Test-Engine** | `Test-Engine` (29) | `docker version --format {{.Server.Version}}` with a 12 s hard limit; returns true when the version matches `d.d` | every 20 s | — | — | host (Docker CLI) | **STAY ON THE HOST** | `docker version` is a host CLI call; the engine's state is not exposed as a file | — |
| **docker_watchdog: Test-Url** | `Test-Url` (121) | `Invoke-WebRequest` of the three port URLs, 6 s timeout | every 20 s | — | — | host (PowerShell HTTP) | **MOVE** for the check itself — it is plain HTTP, reachable from any container | the port values are already in `docker_watchdog.csv` |
| **docker_watchdog: Get-VmMemory** | `Get-VmMemory` (95) | `wsl.exe -d docker-desktop` reads `/proc/meminfo` (MemAvailable, SwapTotal, SwapFree) and `/proc/pressure/memory` + `/proc/pressure/io` | every 20 s (when engine answers) | — | — | host (WSL) | **STAY ON THE HOST** | reads the WSL docker-desktop distro's procfs from the host; no in-container equivalent | — |
| **docker_watchdog: Write-VmSample** | `Write-VmSample` (106) | appends one CSV row per engine-alive probe to `docker_vm.csv`; checks VM memory pressure | every 20 s | appends row; fires the 15-min pressure notice | — | host (WSL + file write) | **STAY ON THE HOST** | file write is host-side; the WSL read is host-only | `docker_vm.csv` is the heartbeat file (mounted at `/d/...` in claude-runner) |
| **docker_watchdog: Save-Snapshot** | `Save-Snapshot` (55) | host RAM, commit charge, CPU 3 s sample, Docker/WSL process table, TCP states, `nvidia-smi` power/utilization, `wsl -l -v`, docker-desktop distro load/PSI/free/dmesg, `docker info`, Docker Desktop log warnings | at the first and third failed engine probe (lines 207, 208) | writes one text file into `docker_snapshots\` | — | host (PowerShell + nvidia-smi + WSL) | **STAY ON THE HOST** | `nvidia-smi`, `Get-Counter`, `Get-CimInstance` are Windows host tools | not a heartbeat — an incident file |
| **docker_watchdog: Restart-DockerDesktop** | `Restart-DockerDesktop` (137) | engine hung 4 probes → stop, `wsl --shutdown`, move stale secrets folder, start, wait, slot restore | every engine-hung episode | restarts Docker Desktop, restores slots | log markers | host (Docker Desktop + WSL + PowerShell) | **STAY ON THE HOST** | restarts the Docker Desktop app on Windows; no in-container equivalent | incident row in `docker_incidents.csv` |
| **docker_watchdog: Add-Incident** | `Add-Incident` (131) | appends an episode row to `docker_incidents.csv` | at each episode end | — | — | host (file write) | **STAY ON THE HOST** | file write is host-side | `docker_incidents.csv` is a heartbeat file |
| **docker_vm** | `docker_vm.csv` (output of `Write-VmSample`, 106–119) | one row per engine-alive probe: `time, avail_mb, swap_used_mb, mem_some10, mem_full10, io_some10, io_full10` | every 20 s (when engine answers) | — | — | host (file) | — (data, not a guard) | it is the board's `vm_memory` panel source | the file itself is the heartbeat |
| **gpu_power_governor** | `gpu_power_governor.py` `Governor.run()` (158) | follows `docker logs -f --since 1s <container>` (default `llama-cpp-server`); parses `selected slot by LCP similarity, f_sim_best = S, f_keep = K` (SEL, line 30) and `prompt processing, n_tokens = X, progress = P` (PROG, line 32) to estimate the uncached part; a boost watchdog thread checks every 10 s that a boost does not outlive `--max-boost-seconds` (default 900 s, line 186) | the stream is continuous; the watchdog thread sleeps 10 s (`watchdog`, line 117); between stream restarts the loop sleeps 10 s (line 177) | boost starts when the uncached part is at least `--min-tokens` (default 4096, line 184) and, if set, at least `--min-fraction` (default 0, line 185); with `--apply` runs `schtasks /run /tn GPU_PL_HIGH` (elevated `nvidia-smi -pl 170`) and releases with `schtasks /run /tn GPU_PL_LOW` (`nvidia-smi -pl 110.5`) on prefill done, after max boost time, on stream end or exit; without `--apply` only logs | log lines only (`gpu_power_governor.log`): start, each boost/release with the uncached size and the current limit, dry-run estimates; no Telegram notices | host, `python gpu_power_governor.py` (dry run) or `--apply` | **STAY ON THE HOST** | uses `nvidia-smi` (power limit read, line 69) and Windows `schtasks` for the elevated `GPU_PL_HIGH` / `GPU_PL_LOW` tasks — both are host-only; the `docker logs -f` call is a host CLI call | last heartbeat: `gpu_power_governor.jsonl` (one JSON line per boost) and `gpu_power_governor.log` (human readable) — both files, readable via the mount |
| **gpu_power_governor: log** | `log()` (36) | timestamped append of every boost/release/dry-run line to `gpu_power_governor.log` | on every event | — | — | host (file) | — (part of governor) | — | `gpu_power_governor.log` is the heartbeat file |
| **gpu_power_governor: _run** | `_run()` (59) | runs the high/low command with a 30 s timeout | on boost / release | returns True on exit 0 | — | host (subprocess) | — (part of governor) | — | — |
| **gpu_power_governor: power_limit** | `power_limit()` (67) | `nvidia-smi --query-gpu=power.limit --format=csv,noheader,nounits` (15 s timeout) | on boost / release / start | reads the current power limit in W | — | host (nvidia-smi) | **STAY ON THE HOST** | `nvidia-smi` is a host CLI tool | — |
| **gpu_power_governor: boost** | `boost()` (75) | — | when the uncached part qualifies | sets `boosted_at`; with `--apply` runs the high command and logs | — | host | — (part of governor) | — | — |
| **gpu_power_governor: release** | `release()` (90) | — | on prefill done / max boost time / stream end / exit | clears `boosted_at`; with `--apply` runs the low command and logs; appends a JSON line to `gpu_power_governor.jsonl` | — | host | — (part of governor) | — | `gpu_power_governor.jsonl` is the heartbeat |
| **gpu_power_governor: watchdog** | `watchdog()` (115) | every 10 s checks whether a boost has outlived `--max-boost-seconds` | 10 s | releases with reason "max boost time" if so | — | host (thread) | — (part of governor) | — | — |
| **gpu_power_governor: qualifies** | `qualifies()` (122) | `uncached >= min_tokens` and (fraction is None or `fraction >= min_fraction`) | on every SEL/PROG line | — | — | host | — (part of governor) | — | — |
| **gpu_power_governor: feed** | `feed()` (125) | parses SEL / REL / PROG lines from the llama-server log stream | on every log line | updates `prev_n`, `est_uncached`, `est_fraction`; triggers `boost` or `release` | — | host | — (part of governor) | — | — |
| **think_archive** | `think_archive.py` `archive()` (15) | pipes `think_model_extract.py` into `docker exec -i claude-runner python -` (timeout 900 s); appends not-yet-archived runs to `think_steps_archive.jsonl` (a still-running run is rewritten once it has a `finished_at`, line 32) | triggered by `queue_watch.archive_think_steps()` every 6 h (or run standalone) | appends new run records; returns (added, total) | prints `archived %d new of %d runs; archive holds %d runs` | host today (`docker exec` from the host + a host-path file write) | **MOVE** | the extract script itself runs inside claude-runner (`/data/runner.sqlite` and `/data/events/*.jsonl` are container paths) and the archive file is written into the mounted `/d/n8n/data-tables/ops` — so with the trigger inside the runner there is no Docker CLI and no Windows dependency; the STAY reading only applies to the host-side form, where the `docker exec` call and the host-path write are host facilities | last heartbeat: `think_steps_archive.jsonl` (file, visible in claude-runner at `/d/n8n/data-tables/ops`) |
| **think_archive: load** | `load()` (38) | reads the last record per run id from `think_steps_archive.jsonl` | on demand (standalone run) | returns `{run_id: record}` | — | host (file read) | — (part of archive) | — | — |

## Container guards planned

- **orphan_guard `main` + all its functions → dashboard orphan-process guard (MOVE).** The guard runs in `ahawr-dashboard`: it lists CLI processes through the NEW runner route `GET /v1/processes` (pid, age, session id, run id, run status) — no `docker exec` and no host process table — and terminates through `POST /v1/processes/{pid}/terminate` once the run has been closed for two checks in a row. The route does not exist today (no `GET /v1/processes` in api.py); plan it in api.py / runs.py. The cancel route (`POST /v1/runs/{run_id}/cancel`, api.py:230) is not used for orphans because their run is already closed, so `cancel()`'s status check (runs.py:538–539) would not match.
- **Interrupt-loop notice → dashboard guard (MOVE).** The "3 quick `interrupted` ends in 15 min" check of `orphan_guard.main` (orphan_guard.py:158–162) moves with the orphan-process guard and reports through the dashboard instead of the host Telegram notice.
- **queue notices → dashboard guard (MOVE).** The notice-only part of `queue_watch.main()` (log tails of `real_queue.log` / `docker_watchdog.log` via `new_lines`/`read_from`, the 20-per-hour cap, `retry_pending`, `last_review`) becomes a dashboard guard reading the mounted files at `/d/n8n/data-tables/ops` and the runner/Postgres sources; it sends the same notices through `notify.send`, which is MOVE (n8n webhook + `ahawr-postgres`).
- **Idle notice → dashboard guard (MOVE), with the queued-only fix.** The 1200 s idle notice (queue_watch.py:243–247) moves into the dashboard and counts runner `running` runs only (not `running` + `queued`), so a queue whose missions are only queued actually produces the 20-minute notice — the gap at queue_watch.py:244–246 (where the else branch resets `idle_since`/`idle_notified` every minute) is closed by the fix.
- **Stalled-run notice → dashboard guard (MOVE).** A notice-only guard: the last event time of `running` Worker / Reviewer runs is read from the runner API (default 20 min, notice once per run); the llama slot state is shown only if the dashboard can read it over HTTP.
- **Stale-queued-run notice → dashboard guard (MOVE).** A notice for a run that stays `queued` too long; optional cancel through the existing `POST /v1/runs/{run_id}/cancel` route (api.py:230).
- **think archive → dashboard guard (MOVE).** `think_archive.py`'s `archive()` runs inside claude-runner (the extract already runs in the container; the jsonl goes to the mounted `/d/n8n/data-tables/ops` folder). A dashboard-side 6-hourly trigger replaces the host `archive_think_steps()` subprocess; no Docker CLI or host-path write remains.
- **RUNNER: terminating the process tree when a run is closed.** The runner change covers every close path (status `finished` / `failed` / `cancelled` / `interrupted`): terminate the CLI process tree (terminate, then kill after a grace, as in `_stop`, runs.py:508–523) and remove the pid from `_procs` (pop at runs.py:420). The root cause of "interrupted while the process lives" — `store.interrupt_active_runs` at startup (store.py:325) plus any other path (a second RunManager/store on the same data dir, a reload, shutdown without waiting for the pump) — is to be closed or documented in Part 2; the orphan guard's `kill`/`kill -9` (orphan_guard.py:94–102) becomes this same route, so a closed run's process tree never outlives the run.
- **STAY ON THE HOST: `queue_alive`, `start_queue`.** These stay host-side: the PowerShell CIM count of the host `bash.exe` (queue_alive), and the `Win32_Process Create` start of `real_queue.sh` (start_queue). The `ensure_relay` check and start move to the container guard TG_RELAY (see Migration of the host watchers).
- **STAY ON THE HOST: docker_watchdog, gpu_power_governor.** docker_watchdog keeps Windows PowerShell (`Get-Process`, `Get-CimInstance`, `Get-Counter`), Docker Desktop control, `wsl.exe --shutdown` / `wsl.exe -d docker-desktop` and `nvidia-smi` in the snapshot; gpu_power_governor keeps `nvidia-smi` and the Windows `schtasks` tasks `GPU_PL_HIGH` / `GPU_PL_LOW`.
- **Dashboard display for the STAY items.** Each host guard writes its heartbeat files to `data-tables/ops` (`orphan_guard.log`, `queue_watch.log`, `queue_watch.state.json`, `docker_watchdog.csv`, `docker_vm.csv`, `docker_incidents.csv`, `think_steps_archive.jsonl`, `gpu_power_governor.log` / `.jsonl`); the runner and retrieval containers already see them at `/d/n8n/data-tables/ops` (compose lines 105–108 and 176–178). `ahawr-dashboard` has no volumes today, so host-file heartbeats need the proposed read-only bind mount into `ahawr-dashboard` (example block from the notes, not applied to `docker-compose.yml`).
- **GET /api/guards (implemented, T016).** The dashboard's `GuardSupervisor` starts in the app lifespan (only when `DASHBOARD_GUARDS=1` starts tasks; the supervisor object itself is always present) and stops at shutdown. The route is read-only like the other `/api` read routes (no `X-AHAWR-Dashboard` header, no bearer) and returns `enabled`, `dry_run`, `running` and, per registered guard, its `name`, `status`, `last_run`, `last_result`, `last_action` and `error_count`; no environment values are returned.

## Migration of the host watchers

Design only; no script, compose file or queue is changed by this section. Source: `claude-runner/docs/guards-notes.md`, sections "Migration research" (line numbers there are from the working tree). Destinations: **CG** = container guard in `ahawr-dashboard`; **HB** = host watcher that stays on the host and writes a heartbeat file; **HL** = host launcher (the only host process that starts or restarts a process); **RT** = retired.

### Duty table

| Script | Duty (file:line) | Dest. | Data source | Guard / env prefix |
|---|---|---|---|---|
| queue_watch.py | Loop every 60 s (`main`, 262) | RT | — | replaced by the guard intervals |
| queue_watch.py | Notice: mission not completed, with last review (204–206, `last_review` 100) | CG | store `events` filtered to `review_parsed` (board_pg.py:489) | QUEUE_NOTICES |
| queue_watch.py | Notice: mission completed (207–209) | CG | store `get_queue_log()` (board_ops_store_pg.py:601) | QUEUE_NOTICES |
| queue_watch.py | Notice: ALERT line, skipping `DISABLE_THINKING`, `new missions were not inserted`, `REAL QUEUE STOPPED` (210–214) | CG | store `get_queue_log()` | QUEUE_NOTICES |
| queue_watch.py | Notice: queue stopped / queue done (210–212, 215–217) | CG | store `get_queue_log()` | QUEUE_NOTICES |
| queue_watch.py | Notice: Docker markers `restarting Docker Desktop`, `containers are back`, `not restarting again` (218–224) | CG | `docker_watchdog.log` via read-only mount (no store copy exists) | QUEUE_NOTICES |
| queue_watch.py | Notice cap 20 per hour and pending retry for 6 h (`notify` 42–48, `retry_pending` 55–66) | CG | in-memory guard state (lost on restart; see Failure rules) | QUEUE_NOTICES |
| queue_watch.py | Read offsets of the two logs (`new_lines` 80, `read_from` 92) and `queue_watch.state.json` | CG | cursor = `queue_log` row id; Docker log offset in guard memory; state file retired | QUEUE_NOTICES |
| queue_watch.py | Idle notice after 1200 s with nothing running (243–251) | CG | runner `running` runs only (not `queued`) and n8n execution count | QUEUE_IDLE |
| queue_watch.py | Count of AHAWR n8n executions (`executions_running` 129) | CG | n8n Postgres query; the credential for it is an open item | QUEUE_NOTICES, QUEUE_RESTART (gate) |
| queue_watch.py | Count of runner runs (`runner_running` 138) | CG | runner API `list_runs` (RunnerClient) | QUEUE_IDLE |
| queue_watch.py | Queue alive check, PowerShell CIM (`queue_alive` 122) | HL | host process table | QUEUE_RESTART decision |
| queue_watch.py | Queue restart: `start_queue` 182, limit 4 per hour, no executions, `queue_end != done` (227–241) | CG writes request, HL starts | request file (see Request launcher design) | QUEUE_RESTART; AHAWR_QUEUE_LAUNCHER_* |
| queue_watch.py | Keep tg_relay running (`ensure_relay` 170–179) | RT | retired: the relay is no longer a host process after the move | — |
| queue_watch.py | Think archive every 6 h (`archive_think_steps` 161–167) | CG | extract runs in claude-runner; archive file in ops mount | THINK_ARCHIVE |
| queue_watch.py | `slot_loop_running` (149) | RT | not called by `main` | — |
| queue_watch.py | Timestamped `queue_watch.log` (`log` 37) | RT | replaced by `GET /api/guards`; HL writes its own heartbeat | — |
| tg_relay.py | Startup check of token and webhook path (`main` 70–75) | CG | token from the container environment (never logged); webhook URL = `DASHBOARD_GUARDS_TG_WEBHOOK_URL` (compose network) | TG_RELAY |
| tg_relay.py | Long poll `getUpdates`, timeout 50 s (86–92) | CG | Telegram API | TG_RELAY |
| tg_relay.py | Forward update to n8n webhook; save offset only after success (57–60, 91–94) | CG | n8n webhook URL on the compose network; offset in `DASHBOARD_GUARDS_TG_OFFSET_PATH`, a file on the mounted volume (not the host `.env`) | TG_RELAY |
| tg_relay.py | Set bot command menu, retried only on exception (51–54, 79, 85) | CG | command list copied from `tg_commands.py` into claude-runner, kept equal by a test that skips when the ops file is absent | TG_RELAY |
| tg_relay.py | Backoff `min(60, 3 * fails)` s (89, 96, 101) | CG | in-guard failure counter | TG_RELAY |
| tg_relay.py | Dry-run: no `getUpdates` polling, no forwarding, no offset write | CG | — | `DASHBOARD_GUARDS_DRY_RUN` |
| orphan_guard.py | List CLI processes via `docker exec ... ps` (`processes` 66–69) | CG | new runner route `GET /v1/processes` (not in api.py today) | ORPHAN_PROCESS |
| orphan_guard.py | List runs (`runs` 84) | CG | runner API directly (not the dashboard HTTP) | ORPHAN_PROCESS |
| orphan_guard.py | Match pid to run by session id; only `claude -p` processes (76–79, 121–131) | CG | runner process and run records | ORPHAN_PROCESS |
| orphan_guard.py | Orphan seen in two consecutive checks (136–139) | CG | in-guard state | ORPHAN_PROCESS (`DASHBOARD_GUARDS_ORPHAN_CONFIRM_CHECKS`) |
| orphan_guard.py | Process younger than 120 s is never touched (133–134) | CG | process start time from runner | ORPHAN_PROCESS (`DASHBOARD_GUARDS_ORPHAN_MIN_AGE_S`) |
| orphan_guard.py | Unknown state never kills; docker or API down means no action (122, 163–165) | CG | — | ORPHAN_PROCESS |
| orphan_guard.py | Kill, then `kill -9` after 10 s (94–102) | CG | new runner route `POST /v1/processes/{pid}/terminate`; escalation in the runner | ORPHAN_PROCESS |
| orphan_guard.py | One notice per kill, at most one per 600 s (142–145) | CG | notifier | ORPHAN_PROCESS |
| orphan_guard.py | Interrupt loop: three quick `interrupted` ends in 900 s, notice only (149–162) | CG | runner run records | INTERRUPT_LOOP |
| orphan_guard.py | Dry run `--once` (113, 137–138) | CG | — | `DASHBOARD_GUARDS_DRY_RUN` |
| orphan_guard.py | `orphan_guard.log` (40) | RT | replaced by `GET /api/guards` | — |
| notify.py | Build notice text and mission tag from the active live-state row (`send` 55, tag 27) | CG | store `list_live_states`; fallback `queue_log` copy | QUEUE_NOTICES |
| notify.py | POST to the n8n webhook; True on 200 (70) | CG | `DASHBOARD_GUARDS_NOTIFY_WEBHOOK_URL` | shared |
| notify.py | Chat id from n8n data table `telegram_chat_id` | CG | open item: needs a read path and credential | shared |
| notify.py | Command-line form `python notify.py "text"` | RT | after docker_watchdog.ps1 VM notice moves to VM_PRESSURE | — |
| docker_watchdog.ps1 | `Test-Engine`, `docker version` with 12 s limit (29) | HB | Docker CLI | — |
| docker_watchdog.ps1 | `Test-Url`, three service URLs, 6 s timeout (121) | CG | plain HTTP from the container; URL list in settings | SERVICE_HEALTH |
| docker_watchdog.ps1 | `Get-VmMemory`, WSL procfs (95) | HB | `wsl.exe -d docker-desktop` | — |
| docker_watchdog.ps1 | `Write-VmSample`, one row per probe to `docker_vm.csv` (106–119) | HB | host file, read by VM_PRESSURE through the mount | — |
| docker_watchdog.ps1 | VM pressure notice once per 15 min (112–117) | CG | `docker_vm.csv` via ops mount | VM_PRESSURE |
| docker_watchdog.ps1 | `Save-Snapshot` incident snapshots (55, 207–208) | HB | Windows and WSL tools | — |
| docker_watchdog.ps1 | `Restart-DockerDesktop` (137–148) | HB | Docker Desktop, WSL | — |
| docker_watchdog.ps1 | `docker restart ahawr-dashboard` (229) | HB | Docker CLI | — |
| docker_watchdog.ps1 | Restart limit 3 per hour, `real_queue.alert` (25, 217) | HB | host files | — |
| docker_watchdog.ps1 | `Add-Incident`, `docker_incidents.csv` (131) | HB | host file | — |
| docker_watchdog.ps1 | Main loop, 20 s pacing (192, 241) | HB | — | — |
| docker_watchdog.ps1 | Heartbeat write, one JSON line (`name`, `time`, `last_action`) to `data-tables/ops/heartbeat/docker_watchdog.json` | HB | host file, read by HOST_HEARTBEAT through the ops mount | — |
| gpu_power_governor.py | `nvidia-smi` power limit read (67–70) | HB | host GPU tool | — |
| gpu_power_governor.py | Heartbeat write, one JSON line (`name`, `time`, `last_action`) to `data-tables/ops/heartbeat/gpu_power_governor.json` | HB | host file, read by HOST_HEARTBEAT through the ops mount | — |
| gpu_power_governor.py | Read `docker logs -f` stream and parse SEL/PROG lines (30, 32, 158–177) | HB | Docker CLI | — |
| gpu_power_governor.py | Boost and release: `schtasks GPU_PL_HIGH` / `GPU_PL_LOW` (75–100, 183–189) | HB | elevated Windows tasks | — |
| gpu_power_governor.py | Watchdog: max boost 900 s (115–119, 186) | HB | — | — |
| gpu_power_governor.py | Thresholds `--min-tokens` 4096, `--min-fraction` (184–185) | HB | — | — |
| gpu_power_governor.py | `gpu_power_governor.log` and `.jsonl` (36–43, 110) | HB | files readable through the ops mount | — |

### Data each container guard reads and where

- Preferred: the board store (`BoardStore`, `DASHBOARD_BOARD_PG_DSN`): `events` (review text), `get_queue_log()` (queue lines), `list_live_states()` (mission tag). No file is read when a store method covers the duty.
- Runner API through `RunnerClient`: runs, processes (new route), terminate (new route), health.
- Host files only through a read-only bind mount of `data-tables/ops` into `ahawr-dashboard`: `docker_watchdog.log`, `docker_vm.csv` and the host watcher heartbeats (`data-tables/ops/heartbeat/`). The container never writes to this mount.
- The container gets no Docker socket, no host shell, no privileged mode and no elevated rights. The Docker socket is root on the host, so it is not mounted under any circumstances. Host process control stays in HL and HB.
- The write paths from the container to the host are exactly two dedicated read-write mounts: the request directory (see below), named by `DASHBOARD_GUARDS_QUEUE_REQUEST_DIR`, and the state directory, named by `DASHBOARD_GUARDS_STATE_DIR`, which holds `DASHBOARD_GUARDS_TG_OFFSET_PATH` (the relay's `tg_relay.offset`). Each holds nothing else.

### Request launcher design

- Queue restart is requested, not executed, by the container. CG `QUEUE_RESTART` writes one file `requests/queue_restart.req` in the directory named by `DASHBOARD_GUARDS_QUEUE_REQUEST_DIR`. The file is written as `queue_restart.req.tmp` and renamed, so the launcher never reads a half-written file.
- Request format: one JSON object `{"request_id": "<uuid4>", "requested_at": "<ISO 8601 with offset>", "reason": "queue_not_alive"}`. The launcher validates the three keys and the types; any other content is discarded and logged.
- HL reads the file every `AHAWR_QUEUE_LAUNCHER_POLL_SECONDS`, and handles a request only when all of these hold:
  - the request is younger than `AHAWR_QUEUE_LAUNCHER_REQUEST_MAX_AGE_S`;
  - no AHAWR n8n execution runs (the same query as queue_watch.py:131), checked by the launcher itself, not trusted from the request;
  - fewer than `AHAWR_QUEUE_LAUNCHER_MAX_PER_HOUR` (4) restarts in the last 3600 s. The restart times are kept in the launcher heartbeat file.
- HL starts the queue with the same command as `start_queue()` (queue_watch.py:182–187). It then removes the request file and writes the result to its heartbeat.
- The launcher is a host process (about 40 lines). It is started by the host watcher starter after queue_watch.py is retired from this duty; `_start_watchers_hidden.ps1` changes only then.

### Launcher or scheduled task

- Option A, request-driven host launcher (chosen): starts only when the container asks, keeps all restart rules in one file, and its state is a plain file. Cost: one more host process and a read-write request mount.
- Option B, Windows scheduled task: runs on a timer without a request and needs its registration outside the repo (no `/create` exists in the repo; `GPU_PL_HIGH` and `GPU_PL_LOW` are the only scheduled tasks). It would poll every interval, even when no restart is wanted, and the restart rules would sit in the task and in the guard.
- Choice: Option A. Reason: the restart decision is made by the container guard that sees the runner and the store, and the host only executes a request that is checked again. A scheduled task cannot see that state without polling.

### Switch order

1. Set the flags for the new guards on the dashboard with dry-run on: `DASHBOARD_GUARDS=1`, `DASHBOARD_GUARDS_DRY_RUN=1`, `DASHBOARD_GUARDS_QUEUE_NOTICES_ENABLED=1`, `DASHBOARD_GUARDS_QUEUE_IDLE_ENABLED=1`, `DASHBOARD_GUARDS_TG_RELAY_ENABLED=1`. The compose change that passes these variables is a Docker topology change: ask first.
2. Keep host queue_watch.py running; the container guard runs in dry-run next to it.
3. Compare the notices for at least one finished mission: the host Telegram notice text and the guard's `last_action` in `GET /api/guards` must match. Record the mission id in the report.
4. Start HL in dry-run and check one request end to end (HL must run before step 5, because the restart duty moves to HL).
5. Stop the host queue_watch.py and stop the host tg_relay.py in the same step (kill by command-line pattern), then turn `DASHBOARD_GUARDS_DRY_RUN` off, so only one relay polls Telegram.

### Rollback

- Flag off: set `DASHBOARD_GUARDS_QUEUE_NOTICES_ENABLED=0` (and the other new flags) in the dashboard environment, then run `docker compose up -d ahawr-dashboard`.
- Host script back: run `D:\Python312\python.exe D:\n8n\data-tables\ops\queue_watch.py` (starts the watcher again; the queue restart duty returns with it). Start `tg_relay.py` again on the host: `D:\Python312\python.exe D:\n8n\data-tables\ops\tg_relay.py`.

### Failure rules

- An unknown state is never a reason to act. A source that does not answer (Postgres, runner, file, mount) gives status `error` and no action: no kill, no queue restart, no request file. An empty or missing process status counts as unknown, not as closed.
- Three errors in a row disable the guard until the dashboard restarts (`DISABLE_AFTER_ERRORS = 3`, guards.py), and send one notice through the notifier (one notice, not one per error).
- Notice cap and pending notices live in guard memory; a dashboard restart drops pending notices. This is accepted and recorded here, not fixed in this section.
- Every new guard is off by default (`DASHBOARD_GUARDS_<NAME>_ENABLED` = 0), so a deploy changes nothing until the flag is set.

### Environment variable names

Fixed here. Later tasks use these names and no others.

| Guard | Variables (`DASHBOARD_GUARDS_<NAME>_...`) | Default |
|---|---|---|
| QUEUE_NOTICES | `ENABLED`, `INTERVAL`, `MAX_PER_HOUR` | off, 60 s, 20 |
| QUEUE_IDLE | `ENABLED`, `INTERVAL`, `AFTER_S` | off, 60 s, 1200 |
| QUEUE_RESTART | `ENABLED`, `INTERVAL` | off, 60 s |
| THINK_ARCHIVE | `ENABLED`, `INTERVAL`, `TIMEOUT_S` | off, 21600 s, 900 |
| ORPHAN_PROCESS | `ENABLED`, `INTERVAL`, `ORPHAN_MIN_AGE_S`, plus existing `ORPHAN_CONFIRM_CHECKS` | off, 30 s, 120, 2 |
| INTERRUPT_LOOP | `ENABLED`, `INTERVAL`, plus existing `INTERRUPT_LOOP_THRESHOLD`, `_WINDOW_S`, `_QUICK_S` | off, 60 s |
| VM_PRESSURE | `ENABLED`, `INTERVAL`, `AVAIL_MB`, `MEM_FULL10`, `REPEAT_S` | off, 20 s, 400, 20, 900 |
| SERVICE_HEALTH | `ENABLED`, `INTERVAL`, `URLS`, `TIMEOUT_S` | off, 20 s, (set in settings), 6 |
| TG_RELAY | `ENABLED`, `INTERVAL`, `POLL_TIMEOUT_S`, `WEBHOOK_URL`, `OFFSET_PATH` | off, 60 s, 50, (set in settings), `<STATE_DIR>/tg_relay.offset` |
| HOST_HEARTBEAT | `ENABLED`, `INTERVAL`, `STALE_AFTER_S`, `HEARTBEAT_DIR` | off, 20 s, 60, `data-tables/ops/heartbeat` |

Shared variables: `DASHBOARD_GUARDS` (master), `DASHBOARD_GUARDS_DRY_RUN`, `DASHBOARD_GUARDS_NOTICE_LIMIT` (existing); new: `DASHBOARD_GUARDS_OPS_DIR` (container path of the ops mount), `DASHBOARD_GUARDS_QUEUE_REQUEST_DIR` (read-write request mount), `DASHBOARD_GUARDS_STATE_DIR` (read-write state mount holding the offset), `DASHBOARD_GUARDS_NOTIFY_WEBHOOK_URL` (n8n webhook as seen from the container; a secret-like value, set in the environment, never in the repo). `DASHBOARD_GUARDS_TG_WEBHOOK_URL` is the n8n webhook URL as seen from the container (compose network) for the TG_RELAY guard.

Host launcher (HL): `AHAWR_QUEUE_LAUNCHER_ENABLED` (off), `AHAWR_QUEUE_LAUNCHER_DRY_RUN` (on), `AHAWR_QUEUE_LAUNCHER_REQUEST_FILE`, `AHAWR_QUEUE_LAUNCHER_MAX_PER_HOUR` (4), `AHAWR_QUEUE_LAUNCHER_REQUEST_MAX_AGE_S` (600), `AHAWR_QUEUE_LAUNCHER_POLL_SECONDS` (60).

Open items (ask first, not done here): compose mounts and environment for `ahawr-dashboard` (ops mount read-only, request directory read-write, notify webhook URL); the runner routes `GET /v1/processes` and `POST /v1/processes/{pid}/terminate`; the n8n Postgres read path and credential for the execution count and the Telegram chat id; the supervisor rule for three errors in a row.

## Operator commands (not run by the mission)

None of the commands in this section was run by the mission that wrote it. The operator runs them in the order below, on the host from `/d/n8n` in Git Bash, unless a step says otherwise. Each step waits for the previous one. Values of the environment variables are set by the operator outside the repository; this section names only the variables.

### 1. Apply the compose patch

Ask first: this is a Docker topology change.

```
git apply --check claude-runner/docs/guards-compose.patch
git apply claude-runner/docs/guards-compose.patch
```

The patch changes `docker-compose.yml`: the guard mounts and the `DASHBOARD_GUARDS_*` variables of `ahawr-dashboard`. It passes `DASHBOARD_GUARDS`, `DASHBOARD_GUARDS_DRY_RUN` and the five `DASHBOARD_GUARDS_<GUARD>_ENABLED` switches from the host environment into the container, plus the fixed paths and `DASHBOARD_GUARDS_TG_WEBHOOK_URL`.

### 2. Set the environment variables (names only)

In the `.env` next to `docker-compose.yml` (the environment that compose reads), set all the variables the patch passes: `DASHBOARD_GUARDS=1`, `DASHBOARD_GUARDS_DRY_RUN=1`, `DASHBOARD_GUARDS_MISSION_NOTICE_ENABLED=1`, `DASHBOARD_GUARDS_IDLE_ENABLED=1`, `DASHBOARD_GUARDS_QUEUE_RESTART_ENABLED=1`, `DASHBOARD_GUARDS_HOST_HEARTBEAT_ENABLED=1`, `DASHBOARD_GUARDS_TG_RELAY_ENABLED=1`, and `DASHBOARD_GUARDS_TG_WEBHOOK_URL` (name only). The compose `environment:` list of `ahawr-dashboard` carries them into the container through the `${NAME:-default}` lines patched in step 1.

`DASHBOARD_TELEGRAM_BOT_TOKEN` and `DASHBOARD_TELEGRAM_CHAT_ID` (the relay's container variables, guards.py:31–32) are not passed by `docker-compose.yml`; the operator adds them to the service's `environment:` list as pass-through lines that read the host `.env`. Their values stay in `.env` and are never written in the repository.

`DASHBOARD_GUARDS_QUEUE_NOTICES_ENABLED` and `DASHBOARD_GUARDS_QUEUE_IDLE_ENABLED` are not names the runner reads: the registered guards are `mission_notice` (guard_queue.py:163), `idle` (guard_queue.py:261), `queue_restart` (guard_restart.py:35), `host_heartbeat` (guard_heartbeat.py:45) and `tg_relay` (dashboard.py:667).

### 3. Recreate the dashboard in a maintenance window

```
docker compose up -d ahawr-dashboard
```

### 4. Dry run for one mission

Keep `data-tables/ops/queue_watch.py` running. When one mission has finished, read the guard state:

```
curl -s http://localhost:8701/api/guards
```

Note the `last_action` of the queue notices guard and the mission id.

### 5. Compare the notices with queue_watch.py

Compare the Telegram notice text that `data-tables/ops/queue_watch.py` sent for that mission with the `last_action` from step 4. Text and mission id must match. If they do not match, stay in dry run and do not go on.

### 6. Switch dry run off

Set `DASHBOARD_GUARDS_DRY_RUN=0` in the environment of `ahawr-dashboard` (the patched compose line `- DASHBOARD_GUARDS_DRY_RUN=${DASHBOARD_GUARDS_DRY_RUN:-1}` reads the host `.env`), then recreate it:

```
docker compose up -d ahawr-dashboard
```

Before step 7, start the host launcher in dry run and check one request end to end:

```
python data-tables/ops/queue_launcher.py --request-dir data-tables/ops/guard_requests --dry-run
```

In dry run the launcher only reads the request file `data-tables/ops/guard_requests/queue_restart.req` and prints the decision; it starts no queue, removes no file and writes no result (queue_launcher.py:15, 103–104).

### 7. Retire the host watchers

```
bash data-tables/ops/retire_host_watchers.sh
```

The script stops `queue_watch.py`, `tg_relay.py` and `orphan_guard.py` by command-line pattern. It prints the edit of `data-tables/ops/_start_watchers_hidden.ps1` and does not apply it.

### 8. Apply the start patch

```
git apply --check claude-runner/docs/guards-start.patch
git apply claude-runner/docs/guards-start.patch
```

The patch makes the edit that step 7 printed, and adds the launcher start. Do not also make that edit by hand. `data-tables/ops/_start_watchers_hidden.ps1` is not tracked by git (`/data-tables/` in `.gitignore`), so `git apply --check` is the only proof that the patch fits the file.

### Rollback

Switch the guards off first, so that no dashboard guard polls Telegram while the host watchers start again:

1. Set `DASHBOARD_GUARDS=0` in the environment of `ahawr-dashboard`, then run `docker compose up -d ahawr-dashboard`.
2. Run `bash data-tables/ops/rollback_host_watchers.sh`. It starts `queue_watch.py`, `orphan_guard.py` and `tg_relay.py` where they are not running, and prints the flags to set by hand in `.env`.
3. Set the flags that the rollback script printed, by hand in `.env`.
4. Revert `data-tables/ops/_start_watchers_hidden.ps1` to the three original `Start-Hidden` lines (the `-` lines of `claude-runner/docs/guards-start.patch`), or reverse the patch with `git apply -R claude-runner/docs/guards-start.patch`.
5. Revert the compose change with `git apply -R claude-runner/docs/guards-compose.patch` (ask first), then run `docker compose up -d ahawr-dashboard`.

`data-tables/ops/retire_host_watchers.sh` and `data-tables/ops/rollback_host_watchers.sh` are in the working tree but not in git. Scripts under `/data-tables/` are ignored by git.

## Guards tab

The dashboard has an optional third tab, Guards, next to Runs and Missions. It is behind the flag `DASHBOARD_GUARDS_TAB` (OFF by default; it needs `DASHBOARD_GUARDS=1`). With the flag off the page, the nav and the `GET /api/guards` answer are exactly as before; with it on, `GET /api/guards` adds `tab: true` and `history_summary`, and the page creates the tab (hash `#guards`) and loads `board_guards_view.js`, `board_guards.js` and `board_guards.css`.

Three views, switched with the buttons at the top (the choice is kept in the browser's localStorage):

- **Cards**: one card per guard (status with a shape, last action, time since the last check, error count, a strip of the last 24 checks) and one per host watcher heartbeat.
- **Flow**: host watchers, the guards of the container and the receivers (Telegram, the n8n webhook, the mission queue) with wires; a wire is lit for 3 seconds after its guard acted.
- **Timelines**: one lane per guard, a dot per check (checks inside one 1% slot merge, the worst result wins; a ringed dot is a check that acted), with a window of 1 hour, 24 hours or 7 days.

History: `GuardSupervisor` keeps the last 500 checks per guard (`guards_history.py`) and saves them to `guards_history.json` in `DASHBOARD_GUARDS_STATE_DIR` (a volume in compose) at most every 60 seconds and at shutdown; a missing or broken file means an empty history. `GET /api/guards/history?window=1h|24h|7d` returns the entries of the window, thinned to 200 per guard (every error and every action is kept); it carries no environment values. All texts of the tab are board i18n keys (`guards.tab.*`, `guards.unit.*`) in English and Russian.
