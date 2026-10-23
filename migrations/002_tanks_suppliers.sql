-- ===========================================================================
-- Migration 002 — developer tank management + supplier payment references
-- (idempotent; safe to re-run)
--   * user_role                    (+ 'dev_support' — the developer's
--                                   support team, may create/calibrate tanks
--                                   and assign them to a company/branch)
--   * supplier_payments.reference  (bank transfer / EFT / cheque reference
--                                   used when confirming an invoice payment)
-- Apply with (as the schema owner — ALTER TYPE needs it):
--   psql -h localhost -U postgres -d fuel_system -f fuel_system/migrations/002_tanks_suppliers.sql
-- ===========================================================================

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_enum e
      JOIN pg_type  t ON t.oid = e.enumtypid
     WHERE t.typname = 'user_role'
       AND e.enumlabel = 'dev_support'
  ) THEN
    ALTER TYPE user_role ADD VALUE 'dev_support';
  END IF;
END $$;

ALTER TABLE supplier_payments ADD COLUMN IF NOT EXISTS reference VARCHAR(100);
