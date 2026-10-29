# ahawr-mcp-board

`ahawr-mcp-board` is an MCP stdio server over the board store. It lets a Claude Code session read the
queue and missions, and make the same plan edits the board UI makes. It uses the same wire format as
`ahawr-mcp-search` (newline-delimited JSON-RPC 2.0 on stdin/stdout, diagnostics on stderr). Code:
`claude-runner/src/claude_runner/mcp_board.py`, entry point `ahawr-mcp-board` in
`claude-runner/pyproject.toml`.

## Tools

| Tool | Kind | What it does |
| --- | --- | --- |
| `queue_list` | read | List the board queue in run order. |
| `mission_get` | read | Return one mission row by `mission_id`. |
| `task_list` | read | Return the per-task status of one mission. |
| `settings_get` | read | Return the claude-code settings row (`hermes_config`). |
| `health` | read | Check that the board store answers. |
| `pause_after_current` | read | Return text that explains how to pause; it does not pause. |
| `dangerous_action_help` | read | Return text that explains how to run stop, pause, rebuild or docker update. |
| `task_edit` | write | Edit the content of one pending task (`fields`: title, objective, acceptance_criteria, verification, scope). |
| `add_hint` | write | Set or clear the Worker hint of one task (`hint: null` clears it). |
| `queue_move` | write | Move a mission to another 0-based queue position (`to_position`). |
| `enable_mission` | write | Enable or disable a mission (`enabled`). |

Every write tool takes `expected_version`, the version the caller read before the change.

## Permissions

- Reads need no confirmation. They return store data and change nothing.
- Writes go through the same checks as the board UI:
  - `expected_version` is required. A mismatch returns `version conflict: reload the plan and retry`
    and the change is not made.
  - The plan guard (`board_api._plan_guard`) refuses a task that already ran or is running. The reply is
    `task already ran or is running and is read-only`.
  - Writes call the store functions (`board_plan.update_task`, `board_plan.set_task_hint`,
    `store.move_queue`, `store.update_mission`), not raw SQL.
- Dangerous actions (stop, pause, rebuild, docker update) are not run, created or confirmed by this
  server. Its tools only explain how to do them. A human requests and confirms them in the dashboard ops
  panel. The `confirm_token` is issued by the dashboard ops API (`board_ops_api.py`), and this server
  never receives or passes one.

## Connect it to a Claude Code session

The DSN is read from the environment variable `DASHBOARD_BOARD_PG_DSN`. Set that variable in the shell
that starts Claude Code. Do not write its value into `.mcp.json` or into the repository.

Example `.mcp.json` in the project root (the value comes from the environment, not from the file):

```json
{
  "mcpServers": {
    "ahawr-board": {
      "command": "ahawr-mcp-board",
      "env": {
        "DASHBOARD_BOARD_PG_DSN": "${DASHBOARD_BOARD_PG_DSN}"
      }
    }
  }
}
```

The server is not added to Worker runs by `claude_cli.py`. It is only for a Claude Code session that a
person starts and that loads this `.mcp.json`.

## Limits

- If `DASHBOARD_BOARD_PG_DSN` is unset or cannot be reached, `get_board_store` returns an unavailable
  store. Tool calls then return an error text with `isError` set, and the server keeps running.
- Writes change the live board. Point the DSN only at the store you mean to change.
- The server does not redact output yet (`store_text` in `mcp_board.py` is a shortcut; redaction waits
  for the provider-secrets work).
- Queue positions are 0-based. The store rejects a conflicting `expected_version`; the caller must read
  the current version again and retry.
- Dangerous actions are never performed through MCP.
- Transport is stdio only; there is no network listener.
