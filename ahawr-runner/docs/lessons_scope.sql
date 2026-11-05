-- AHAWR Board — lessons scope column on board.lessons (Postgres)
--
-- Proposal only. NOT APPLIED to any database. Apply after
-- ahawr-runner/docs/board-lessons.sql. All statements are idempotent.
--
-- Values and promotion rule: ahawr-runner/docs/lessons.md ("Scopes and promotion").
-- Open point: the operator mark that promoted_scope() takes as input has no column yet;
-- it needs a decision (for example a nullable column) before automatic promotion runs.

ALTER TABLE board.lessons ADD COLUMN IF NOT EXISTS scope text NOT NULL DEFAULT 'mission';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'lessons_scope_check'
    ) THEN
        ALTER TABLE board.lessons
            ADD CONSTRAINT lessons_scope_check CHECK (scope IN ('mission', 'project'));
    END IF;
END
$$;
