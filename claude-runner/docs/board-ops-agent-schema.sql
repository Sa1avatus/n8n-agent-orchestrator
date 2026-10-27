-- AHAWR Board — operations tables: idempotent fill for missing tables
--
-- Copies the definitions of board.commands, board.agent, board.queue_log and
-- board.host_snapshot from claude-runner/docs/board-ops-schema.sql.  Only
-- CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS — it neither
-- removes nor modifies existing tables, and it touches nothing on n8n
-- tables.  The board schema itself is created by board-schema.sql.
--
-- Operator step, NOT RUN in this task.

-- One row per command.  status: new -> running -> done / failed / rejected
-- (rejected is written at admission or claim time, not after execution).
CREATE TABLE IF NOT EXISTS board.commands (
    id            bigserial PRIMARY KEY,
    action        text NOT NULL,
    args_json     jsonb,
    status        text NOT NULL
                  CHECK (status IN ('new', 'running', 'done', 'failed', 'rejected')),
    requested_by  text,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    result_json   jsonb,
    confirm_token text
);

CREATE INDEX IF NOT EXISTS commands_requested_at_idx
    ON board.commands (requested_at);
CREATE INDEX IF NOT EXISTS commands_status_idx
    ON board.commands (status);

-- Invariant: at most one command with status = 'running'.  Terminal states
-- (done / failed / rejected) and the new state are unrestricted.  The index
-- key is the status column, so any two rows both in the 'running' state are
-- duplicates of the key value and the second one is a unique violation.
-- The agent claim protocol (board-ops.md "Command protocol") checks the
-- running count in the same transaction; this partial unique index makes
-- the invariant unbreakable at the database level as well.
CREATE UNIQUE INDEX IF NOT EXISTS commands_one_running_idx
    ON board.commands ((status))
    WHERE status = 'running';

-- Agent heartbeat, one row per poll, newest first.
CREATE TABLE IF NOT EXISTS board.agent (
    id            bigserial PRIMARY KEY,
    heartbeat_at  timestamptz NOT NULL DEFAULT now(),
    version       text,
    host_info_json jsonb
);

CREATE INDEX IF NOT EXISTS agent_heartbeat_at_idx
    ON board.agent (heartbeat_at);

-- Published tail of real_queue.log, one row per log line.
CREATE TABLE IF NOT EXISTS board.queue_log (
    id    bigserial PRIMARY KEY,
    at    timestamptz NOT NULL DEFAULT now(),
    line  text NOT NULL
);

CREATE INDEX IF NOT EXISTS queue_log_at_idx ON board.queue_log (at);

-- Published watcher/environment snapshot, one row per agent poll.
CREATE TABLE IF NOT EXISTS board.host_snapshot (
    id        bigserial PRIMARY KEY,
    at        timestamptz NOT NULL DEFAULT now(),
    data_json jsonb
);

CREATE INDEX IF NOT EXISTS host_snapshot_at_idx ON board.host_snapshot (at);
