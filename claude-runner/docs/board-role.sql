-- AHAWR Board — Postgres role for the board (least privilege)
--
-- The board needs only reads plus writes on its own schema (board.*),
-- plus SELECT/UPDATE/INSERT on the four n8n mission data tables.  Those
-- tables live in the public schema under their physical
-- data_table_user_* names (see claude-runner/docs/board-data-model.md).
--
-- The role is a LOGIN role so the dashboard can connect as board_operator.
-- Its password is a placeholder (CHANGE_ME); the operator replaces it with
-- the real password at deploy time, e.g.:
--     ALTER ROLE board_operator PASSWORD '<real-password>';
--
-- No GRANT on public.missions / public."Live State" / public."Event log" /
-- public.hermes_config beyond SELECT/UPDATE/INSERT; no DDL, no other
-- objects, no superuser.

-- Idempotent role creation: skip if the role already exists.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'board_operator') THEN
        CREATE ROLE board_operator LOGIN PASSWORD 'CHANGE_ME';
    END IF;
END
$$;

-- Board tables: full DML (SELECT/INSERT/UPDATE/DELETE).
GRANT USAGE ON SCHEMA board TO board_operator;
GRANT SELECT, INSERT, UPDATE, DELETE ON
    board.templates,
    board.queue,
    board.mission_config,
    board.task_overrides,
    board.changelog,
    board.flags,
    board.preflight
    TO board_operator;

GRANT USAGE ON SEQUENCE board.changelog_id_seq TO board_operator;

-- n8n mission data tables (public schema, physical names):
--   missions       = data_table_user_hYAqO52ZGHXxhhHX
--   Live State     = data_table_user_Yn9lOhVhQ9gFymNs
--   Event log      = data_table_user_ceJ7PFzVJvPN0LsV
--   hermes_config  = data_table_user_1S21PsMqT0FpR7eD
GRANT SELECT, INSERT, UPDATE ON
    public."data_table_user_hYAqO52ZGHXxhhHX",
    public."data_table_user_Yn9lOhVhQ9gFymNs",
    public."data_table_user_ceJ7PFzVJvPN0LsV",
    public."data_table_user_1S21PsMqT0FpR7eD"
    TO board_operator;

-- Sequences backing the identity `id` columns of the four mission data tables.
GRANT USAGE, SELECT ON SEQUENCE
    public."data_table_user_hYAqO52ZGHXxhhHX_id_seq",
    public."data_table_user_Yn9lOhVhQ9gFymNs_id_seq",
    public."data_table_user_ceJ7PFzVJvPN0LsV_id_seq",
    public."data_table_user_1S21PsMqT0FpR7eD_id_seq"
    TO board_operator;
