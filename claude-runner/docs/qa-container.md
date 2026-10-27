# QA in its own container

Status: built and run once on 2026-10-23 (see "First run" at the end); the host runs UI QA until the in-process stack is at parity. The text below was written before the build. Every operator step is
marked NOT RUN. The files named here as planned (`claude-runner/qa/Dockerfile.qa`,
`claude-runner/qa/compose-qa.patch`, `data-tables/ops/_retire_host_qa_worker.sh` and its rollback script) were not
present in the workspace when this document was written; they must exist before the operator steps can run.

## Architecture before

- The QA run is started by the host worker `data-tables/ops/qa_worker.py` (outside git). It claims one request from
  the runner queue at `http://127.0.0.1:8700` (`CLAUDE_RUNNER_URL`, default in the script), finds the changed files with
  git in the mission working directory, and runs `python -m claude_runner.qa run` on the host.
- The runner port is published to the host: `docker-compose.yml` `claude-runner` service, `ports: "127.0.0.1:8700:8700"`
  (comment at docker-compose.yml:118 and 129 says it is for the host QA worker).
- Browser scenarios need a throwaway stack. `claude_runner/qa_stack.py` `stack_up(fixture)` starts a fresh Postgres and
  a dashboard with `docker` commands (`qa_stack.py:341`), with the fixture chosen from `QA_FIXTURES = ("default", "empty")`
  (`qa_stack.py:26`). `stack_down` removes containers, volume and network by the stack id.
- The host therefore needs the Docker CLI with access to the Docker daemon, and the host git checkout.

## Architecture after

- A new service `ahawr-qa` (planned, `claude-runner/qa/Dockerfile.qa`, `claude-runner/qa/compose-qa.patch`) runs
  QA in a Linux container with Playwright, pytest, ruff, mypy and node. It is built from its own Dockerfile, not from
  the runner image.
- The host worker is retired: the runner queue is polled from inside the compose network at
  `http://claude-runner:8700`. The runner no longer needs a host-published port, so the `127.0.0.1:8700:8700`
  mapping is removed (close the port).
- Two stack modes are available: the existing Docker stack (`docker`) and a light in-process stack (`inprocess`) that
  runs the dashboard in the same process for the UI and API layers. See "Stack modes".
- Verdicts are compared on the host against the container before the host worker is retired (operator steps).

## Rights table

Planned mounts and rights for the `ahawr-qa` container. "Who may write" lists every writer.

| Path inside the container | Source | Mode | Who may write |
|---|---|---|---|
| `/repo` (repository) | the git checkout | ro | nobody |
| mission working directories | the mission worktrees | ro | nobody |
| `/qa-runs` | named volume | rw | the QA run only (verdicts, screenshots, logs of a run) |
| `/ops/heartbeat` | `data-tables/ops/heartbeat` | rw | the QA worker heartbeat file only (`qa_worker.json`) |
| root filesystem | image | read_only | nobody (image is immutable at run time) |
| `/tmp` | tmpfs | rw | the QA run, lost on exit |
| home directory | tmpfs | rw | the QA run, lost on exit |

The container gets no Docker socket, no host shell, no privileged mode and no added capabilities. The only two
read-write host paths are `/qa-runs` and the heartbeat directory, and the heartbeat mount holds nothing else.

## No Docker socket

The Docker socket is root on the host, so it is not mounted under any circumstances. Consequences:

- The container cannot start or stop other containers. The browser stack in the `inprocess` mode runs inside the
  container, so no Docker call is needed.
- Real Postgres or n8n would need a Docker daemon for their own containers. The planned way is an isolated
  docker-in-docker sidecar on a private network, with no path to the host daemon. This is NOT done here, and it is
  a change of the stack topology, so it needs approval before it is built.

## Stack modes

Two modes exist in the design; each run uses exactly one.

- `docker`: the current `qa_stack.stack_up` path (fresh Postgres and dashboard in containers, torn down by id).
  Chosen by default on the host, where the Docker CLI is available.
- `inprocess`: the dashboard runs in the QA process, with the same fixtures. Chosen inside `ahawr-qa`, where there is
  no Docker socket.

How a mode is chosen (planned): the `--stack {docker,inprocess}` option of `python -m claude_runner.qa`, and the
environment variable `QA_STACK` when the option is absent. The option wins over the variable. An unknown value is
refused with an error, not defaulted. The variable name is a proposal until it is in code.

## The postgres mode (2026-10-26, the mode of `ahawr-qa`)

The `inprocess` mode seeds a light in-memory store: it has no event rows, so a mission has no started time, task
statuses or attempts, and the queue has no version. Nine of the 17 UI scenarios failed on it for that reason (the rest
of the failures were a real race in the Kanban view, fixed in `board_view_kanban.js`).

The third mode, `postgres`, uses the same seed SQL as the `docker` mode on a throwaway server that already runs: the
compose service `qa-postgres` (profile `qa-container`, no published port, data in memory, password `QA_PG_PASSWORD`
from `.env`). Each stack creates its own database `qa_<id>` there, starts the dashboard as a child process of the QA
worker and drops the database on teardown. The host name must pass `assert_qa_target` (`qa-` prefix, never a production
name). No Docker access is needed. `ahawr-qa` sets `QA_STACK_MODE=postgres`, `QA_PG_HOST=qa-postgres`.

Result in the container (2026-10-26): 17 of 17 UI scenarios, 49 of 49 API checks, the smoke crawl passes, a real
request through the runner is answered. The regression baseline (`qa/baselines/claude-runner.json`, taken inside the
container) lists 19 known pytest failures; only new failures fail the regression layer.

## Dashboard QA fixture setting

`DASHBOARD_QA_FIXTURE` (planned) selects a seeded fixture for the dashboard. It is OFF by default: when unset or
empty, the dashboard behaves exactly as before. It is refused together with a database DSN (`DASHBOARD_BOARD_PG_DSN`):
a seeded fixture must never run against a real database, so the dashboard exits with an error when both are set.

## Operator steps

Each step is NOT RUN. Run them in this order on the host, and record the output of each in the mission report.

1. NOT RUN: build the image: `docker compose build ahawr-qa` (uses `claude-runner/qa/Dockerfile.qa`).
2. NOT RUN: start the service: `docker compose up -d ahawr-qa`.
3. NOT RUN: check the container: `docker compose exec ahawr-qa python -m claude_runner.qa run --tag ui` and confirm
   the root filesystem is read-only and no Docker socket is present (`test ! -S /var/run/docker.sock`).
4. NOT RUN: parity on the host: run the 15 scenarios in both stack modes on the host and compare the verdicts
   of the container with those of the host worker. Note: `claude-runner/qa/scenarios/` now holds 17 YAML files, so the
   list of the 15 must be fixed in the mission before the run.
5. NOT RUN: retire the host worker with the printed script `data-tables/ops/_retire_host_qa_worker.sh` (planned). It
   stops the host watcher and prints the next command; it does not delete files.
6. NOT RUN: close the published port: remove the `ports` mapping of `claude-runner` (`127.0.0.1:8700:8700`) in
   `docker-compose.yml`, then `docker compose up -d claude-runner`. This restarts the runner, so it is an operator action.
7. NOT RUN: rollback, if needed: run the rollback script `data-tables/ops/_rollback_host_qa_worker.sh` (planned, the
   counterpart of step 5) to restart the host worker, and restore the port mapping from `compose-qa.patch`.

## Not done

- The runner port 8700 stays published on 127.0.0.1: the operator scripts and the host-actions executor on the host
  call it. Closing it needs those moved first.
- The nightly run inside the container is not wired.
- The 19 baseline pytest failures are not fixed (environment ones: tests that need the process-group rules of the
  Windows host, a writable repository; old ones: the escaping check of `board_guards.js`, the static whitelist test).
- The host worker is retired (stopped); `qa_worker.py` and `_start_qa_worker.ps1` stay as the rollback.

## First run (2026-10-23)

- Image built, container started with `docker compose --profile qa-container up -d ahawr-qa`; no Docker socket,
  read-only root, the runner reached over the stack network, heartbeat written.
- Found and fixed: the QA data was looked up beside the installed copy (`PYTHONPATH` now points at the
  repository mount); verdicts need the writable `QA_RUNS_DIR=/qa-runs` volume.
- API layer in the container: 49 of 49 pass. UI layer in the `inprocess` mode: 8 of 17 pass; the nine failures
  (Kanban queue order after a move, the stale-version message, Grid sort, the launch settings model field)
  pass on the Docker stack. The cause was not found: a move through the API and through a drag both work
  against the in-process dashboard in a script, but the scenario runner sees no change. Until this is fixed
  the container is not used (compose profile `qa-container`, runner port 8700 published on 127.0.0.1).
- Not done: the regression baseline (`qa baseline claude-runner` needs a writable `qa/baselines` mount) and
  the retirement of the host worker.
