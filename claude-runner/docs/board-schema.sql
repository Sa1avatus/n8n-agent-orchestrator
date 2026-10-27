-- AHAWR Board — schema `board` (Postgres)
--
-- Read-only data model reference: claude-runner/docs/board-data-model.md.
-- All statements are idempotent (IF NOT EXISTS) so this file can be applied
-- more than once.  It creates the board schema and its tables.

CREATE SCHEMA IF NOT EXISTS board;

-- Mission template.
-- One row per reusable mission template; source_mission_id points at the
-- mission_id of the mission whose plan the template was built from.
CREATE TABLE IF NOT EXISTS board.templates (
    name              text PRIMARY KEY,
    source_mission_id text,
    config_json       text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS templates_source_mission_id_idx
    ON board.templates (source_mission_id);

-- Ordered list of missions to run.
-- enabled=true means the mission participates in the queue;
-- attempts_per_task caps the attempts for this mission's tasks;
-- retry_failed=true means failed missions are retried even outside the
-- default retry list.
CREATE TABLE IF NOT EXISTS board.queue (
    position         integer PRIMARY KEY,
    mission_id       text NOT NULL UNIQUE,
    enabled          boolean NOT NULL DEFAULT true,
    attempts_per_task double precision,
    retry_failed     boolean NOT NULL DEFAULT false,
    note             text
);

CREATE INDEX IF NOT EXISTS queue_enabled_idx
    ON board.queue (enabled);

-- Per-mission configuration overrides (values that differ from the
-- hermes_config defaults).  config_json is the JSON object of overrides.
CREATE TABLE IF NOT EXISTS board.mission_config (
    mission_id   text PRIMARY KEY,
    config_json  text NOT NULL,
    updated_at   timestamptz NOT NULL DEFAULT now(),
    updated_by   text
);

-- Per-task overrides: extra hints / parameters for one task of one mission.
-- PK is (mission_id, task_key); task_key is the task id, e.g. 'T003'.
CREATE TABLE IF NOT EXISTS board.task_overrides (
    mission_id  text NOT NULL,
    task_key    text NOT NULL,
    hint        text,
    extra_json  text,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (mission_id, task_key)
);

CREATE INDEX IF NOT EXISTS task_overrides_mission_idx
    ON board.task_overrides (mission_id);

-- Audit log of board mutations.  before_json / after_json hold the JSON
-- value of the mutated field before and after the change; they are NULL
-- when the action does not carry a field value (e.g. a flag set).
CREATE TABLE IF NOT EXISTS board.changelog (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    who         text,
    action      text NOT NULL,
    mission_id  text,
    task_key    text,
    before_json text,
    after_json  text
);

CREATE INDEX IF NOT EXISTS changelog_at_idx ON board.changelog (at);
CREATE INDEX IF NOT EXISTS changelog_mission_idx ON board.changelog (mission_id);

-- Simple key/value flags (e.g. pause, dry_run, accept_requested).
-- PK is the key.  value is text so any flag kind is supported.
CREATE TABLE IF NOT EXISTS board.flags (
    key   text PRIMARY KEY,
    value text NOT NULL
);

CREATE INDEX IF NOT EXISTS flags_value_idx ON board.flags (value);

-- Preflight results captured before a mission is started.
-- items_json is the JSON array of preflight items with their results.
CREATE TABLE IF NOT EXISTS board.preflight (
    mission_id  text NOT NULL,
    checked_at  timestamptz NOT NULL DEFAULT now(),
    items_json  text NOT NULL,
    PRIMARY KEY (mission_id, checked_at)
);

-- Project rules: ordered list of file-name patterns (``*`` is a wildcard).
-- The whole list is replaced on each save; updated_at of the rows is the
-- version (max over the rows, '' when the list is empty).
CREATE TABLE IF NOT EXISTS board.project_rules (
    name        text PRIMARY KEY,
    pattern     text NOT NULL,
    position    integer NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Dashboard UI languages.  enabled=false hides the language from the picker.
CREATE TABLE IF NOT EXISTS board.i18n_languages (
    code     text PRIMARY KEY,
    name     text NOT NULL,
    enabled  boolean NOT NULL DEFAULT true
);

-- UI strings per language.  An empty text is never stored: it deletes the row.
CREATE TABLE IF NOT EXISTS board.i18n_strings (
    lang        text NOT NULL,
    key         text NOT NULL,
    text        text NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text,
    PRIMARY KEY (lang, key)
);

-- Providers (claude-runner/docs/providers.md, section 9).  Holds no secret: the API key
-- lives in the secrets file, keyed by name.  search_first / retry_fresh_session NULL = the
-- CLAUDE_RUNNER_* default; tools is the TOOLS string as written in an env block.
CREATE TABLE IF NOT EXISTS board.providers (
    name                   text PRIMARY KEY CHECK (name ~ '^[a-z0-9][a-z0-9_-]*$'),
    label                  text,
    api_type               text,
    base_url               text,
    enabled                boolean NOT NULL DEFAULT true,
    context_tokens         integer,
    compact_pct            double precision,
    compact_min_pct        double precision,
    max_output_tokens      integer,
    tools                  text,
    search_first           boolean,
    retry_fresh_session    boolean,
    notes                  text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

-- Models of a provider (providers.md, section 1).  api_type NULL = the provider's api_type.
-- capability_overrides / probe_result are JSON objects; price fields are USD per million tokens.
CREATE TABLE IF NOT EXISTS board.provider_models (
    id                       bigserial PRIMARY KEY,
    provider                 text NOT NULL REFERENCES board.providers (name) ON DELETE CASCADE,
    model                    text NOT NULL,
    alias                    text,
    price_input_per_mtok_usd  double precision,
    price_output_per_mtok_usd double precision,
    supports_thinking        boolean NOT NULL DEFAULT false,
    api_type                 text,
    capability_overrides     jsonb,
    probe_result             jsonb,
    probed_at                timestamptz,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (provider, model)
);
