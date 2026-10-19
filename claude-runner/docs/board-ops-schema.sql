-- AHAWR Board — operations tables and grants (Postgres)
--
-- Data model reference: claude-runner/docs/board-ops.md ("Tables" section).
-- All statements are idempotent (IF NOT EXISTS / DO $ blocks) so this file
-- can be applied more than once.  The board schema itself is created by
-- board-schema.sql; this file only adds the operations tables.
--
-- Roles: the two login roles below are placeholders.  The operator replaces
-- them with the real role names at deploy time and re-runs this file.  The
-- dashboard role is the one the browser's API connects as; the agent role is
-- the one the host agent (board_agent.py) connects as.

CREATE SCHEMA IF NOT EXISTS board;

-- Dashboard role (writer of commands and settings; read-only on everything
-- else in board).  Placeholder name.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'board_dashboard_role') THEN
        CREATE ROLE board_dashboard_role LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END
$$;

-- Agent role (executor of commands; heartbeat and snapshot publisher).
-- Placeholder name.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'board_agent_role') THEN
        CREATE ROLE board_agent_role LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END
$$;

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

-- Operator-tunable settings; value_json is the validated value.
-- updated_at doubles as the optimistic-locking version (expected_version).
CREATE TABLE IF NOT EXISTS board.settings (
    key         text PRIMARY KEY,
    value_json  jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    version     integer NOT NULL DEFAULT 0
);

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

-- ============ GRANTS: dashboard role ============
-- The dashboard writes commands and settings; it reads everything else.
GRANT USAGE ON SCHEMA board TO board_dashboard_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON board.commands TO board_dashboard_role;
GRANT USAGE ON SEQUENCE board.commands_id_seq TO board_dashboard_role;

GRANT SELECT, INSERT, UPDATE, DELETE ON board.settings TO board_dashboard_role;

GRANT SELECT ON
    board.agent,
    board.queue_log,
    board.host_snapshot
    TO board_dashboard_role;

-- ============ GRANTS: agent role ============
-- The agent claims and executes commands, and publishes the heartbeat,
-- log tail and host snapshot.  It never writes settings.
GRANT USAGE ON SCHEMA board TO board_agent_role;

GRANT SELECT, UPDATE ON board.commands TO board_agent_role;
GRANT USAGE ON SEQUENCE board.commands_id_seq TO board_agent_role;

GRANT SELECT ON board.settings TO board_agent_role;

GRANT SELECT, INSERT ON
    board.agent,
    board.queue_log,
    board.host_snapshot
    TO board_agent_role;

GRANT USAGE ON SEQUENCE
    board.agent_id_seq,
    board.queue_log_id_seq,
    board.host_snapshot_id_seq
    TO board_agent_role;
