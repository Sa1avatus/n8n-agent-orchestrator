# ahawr_search as an MCP tool for the local Worker

## Purpose

A finding of 15.10.2026 was that the Worker calls `ahawr-search` in about a third of its runs.
The other runs search with grep, rg or find, or not at all. The MCP option gives the local Worker the
search as a native tool, `mcp__ahawr__ahawr_search`, so the model can reach for it without a Bash command.
Whether this raises the share of runs that search, and whether it changes the outcome, is not known yet.
The A/B procedure below decides that.

## How the server works

`ahawr-mcp-search` (`claude-runner/src/claude_runner/mcp_search.py`, entry point in `claude-runner/pyproject.toml`)
is a stdio MCP server. It speaks newline-delimited JSON-RPC 2.0: protocol messages go to stdout, diagnostics to stderr.

- `initialize` and `tools/list` advertise one tool, `ahawr_search`, with the arguments `query` (required),
  `k` (fragments, default 8) and `budget` (token budget).
- `tools/call` delegates to `search_cli.search_text`, the same code as the `ahawr-search` command, and
  searches the Worker's working directory against the retrieval service (`AHAWR_RETRIEVAL_URL`).
- A retrieval outage comes back as ordinary tool text (`retrieval unavailable: ...`), not as a protocol error.
- Bad arguments (empty query, non-integer `k` or `budget`) get a JSON-RPC `-32602` error.

Claude Code starts the server from a `--mcp-config` file that `claude_cli.mcp_search_args` writes
(`mcpServers.ahawr`, command `ahawr-mcp-search`, no environment and no secrets).

Usage counting: `retrieval-service/src/ahawr_retrieval/usage.py` counts an MCP call
(`MCP_SEARCH_TOOL = "mcp__ahawr__ahawr_search"`) the same way as a Bash `ahawr-search` command, in the
`ahawr_search_calls` field of the usage report.

## The flag

- Name: `CLAUDE_RUNNER_MCP_SEARCH` (`claude-runner/src/claude_runner/config.py`, default `False`).
- Default: off. `docker-compose.yml` passes `CLAUDE_RUNNER_MCP_SEARCH=${CLAUDE_RUNNER_MCP_SEARCH:-0}`
  to the `claude-runner` service, so an unset value is `0`.
- Scope: the local Worker only. `mcp_search_args` adds the MCP config only when the role is `worker` and the
  provider is `LOCAL`. Architect, Reviewer and cloud-provider runs are unchanged with the flag on.
- With the flag off, no MCP config is written and the command line is byte-for-byte unchanged.

## How to enable (operator)

1. In the host `.env`, set `CLAUDE_RUNNER_MCP_SEARCH=1`. Do not commit `.env`.
2. Recreate the runner so it reads the new value: `docker compose up -d claude-runner`.
3. Check the setting in the container, without printing other variables:
   `docker compose exec claude-runner sh -c 'test "$CLAUDE_RUNNER_MCP_SEARCH" = 1 && echo on'`.
4. To turn it off, set the value back to `0` (or remove the line) and recreate `claude-runner` again.

## A/B procedure (not run)

Status: the A/B has NOT been run. No measurement exists yet; the numbers below are the plan.

Design:

- Task set: one fixed set of Worker tasks, the same tasks in both arms, in the same order, on the same
  workspace snapshot. Use only local-provider Worker runs.
- Arm A: `CLAUDE_RUNNER_MCP_SEARCH=0`. Arm B: `CLAUDE_RUNNER_MCP_SEARCH=1`. Everything else identical.
  Run the arms in alternating blocks, not one after the other, so that a change in the model or the
  corpus does not fall into one arm only.
- Minimum sample: at least 30 Worker runs per arm (60 in total). Below that, do not decide.

Metrics:

1. Search share: the share of runs with at least one search, where a search is an `ahawr-search` Bash
   command or an `mcp__ahawr__ahawr_search` call (the `ahawr_search_calls` count from the usage report).
   Report it per arm, with the run count.
2. Attempt acceptance rate: accepted attempts divided by all Worker attempts, per arm, using the review
   outcome stored per attempt (`review_status`). Report it per arm, with the attempt count.

Decision criterion (proposed, to be confirmed before the run):

- Adopt (set the default to `1` in a later change) only if both arms have at least 30 runs, the search share
  in arm B is at least 15 percentage points above arm A, and the acceptance rate in arm B is not more than
  5 percentage points below arm A.
- Otherwise keep the default `0`. If the search share rises but acceptance falls, keep `0` and record it.
- If either arm has fewer than 30 runs, the result is "no decision"; extend the task set and run again.

Record the result (both arms, the run and attempt counts, the decision) under a "Results" heading at the end
of this file, once the run has happened.

## Limits

- The MCP server is only in the local Worker's container. It searches the same corpus as `ahawr-search`;
  it does not add new sources.
- The tool costs a retrieval call per search, as the Bash command does.
