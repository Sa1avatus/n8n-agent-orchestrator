# Adding the roles_json column

This is an operator guide. None of the steps below has been run; they describe what to do, not what was done.

## What to add

| Item | Value |
|---|---|
| Table | `hermes_config` (n8n data table; the `claude-code` profile row is the one the runner reads) |
| Column | `roles_json` |
| Type | string (long text) |
| Default | empty (no value) |

`board.mission_config` needs nothing. It is a key-value table (one row per `mission_id`, JSON in `config_json`), and the per-mission override is the key `roles_json` inside that JSON. No schema change is required there.

## Steps (not run)

1. Open the n8n interface and go to Data tables, then `hermes_config`. Step status: not run.
2. Add a column named `roles_json`, type string (long text), with an empty default. Step status: not run.
3. Save the table. Step status: not run.
4. Confirm the column appears in the table header and that the `claude-code` row still has all its other values unchanged. Step status: not run.

If the n8n interface offers a CLI or API for data tables, the same column can be added through it, but the operator must check the n8n version first. Step status: not run.

## Why no raw SQL

Do not add the column with `ALTER TABLE` or any other raw SQL. n8n keeps its own metadata for each data table (the column list and types the interface and the workflow nodes read). A column added behind n8n's back is missing from that metadata, so the table view, the write nodes and the Telegram field checks can disagree with the real table. Add columns in the n8n interface only.

## The readers work without the column

The runner and the workflow must keep working before the column exists:

- `PgBoardStore.hermes_config` and `BoardStore` callers read the row as a dict; an absent `roles_json` key is read as "no roles", and the next precedence level applies (see `roles-config.md`, section Precedence).
- An empty `roles_json` or an absent column falls back to the legacy `*_provider` / `*_model` columns.

So the column can be added at any time, and the rollout does not need a restart to stay safe. Until the column exists, the behaviour is the same as before the change.

## Checks after the change (not run)

- In n8n, the `hermes_config` table shows `roles_json` with an empty value on the `claude-code` row. Not run.
- The runner still starts and reads the `claude-code` row without error with the column present. Not run.
- With the column absent, the runner still starts (this is the state before the step above). Not run.
