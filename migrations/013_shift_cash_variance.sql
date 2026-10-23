ALTER TABLE shifts
    ADD COLUMN IF NOT EXISTS system_cash_amount NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS counted_cash_amount NUMERIC(14,2);

ALTER TABLE shift_attendance
    ADD COLUMN IF NOT EXISTS system_cash_amount NUMERIC(14,2),
    ADD COLUMN IF NOT EXISTS counted_cash_amount NUMERIC(14,2);
