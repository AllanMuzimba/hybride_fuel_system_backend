-- A company registered by the dev_support team stays inactive until a
-- developer confirms it (POST /companies/{id}/confirm).
BEGIN;

ALTER TABLE companies ADD COLUMN IF NOT EXISTS registered_by INTEGER REFERENCES users(id);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS confirmed_by  INTEGER REFERENCES users(id);
ALTER TABLE companies ADD COLUMN IF NOT EXISTS confirmed_at  TIMESTAMPTZ;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS needs_confirmation BOOLEAN NOT NULL DEFAULT FALSE;

COMMIT;
