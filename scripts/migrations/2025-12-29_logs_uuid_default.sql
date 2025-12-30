-- Ensure logs.id has a default UUID and backfill nulls
-- Requires pgcrypto for gen_random_uuid(); enable if not present
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Backfill any existing rows with null ids
UPDATE logs SET id = gen_random_uuid() WHERE id IS NULL;

-- Set default and keep not null constraint
ALTER TABLE logs ALTER COLUMN id SET DEFAULT gen_random_uuid();
