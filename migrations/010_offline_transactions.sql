-- Request IDs make retries of offline financial writes idempotent.
ALTER TABLE fuel_receipts ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fuel_receipts_client_request_id ON fuel_receipts(client_request_id);
ALTER TABLE supplier_payments ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_supplier_payments_client_request_id ON supplier_payments(client_request_id);
ALTER TABLE customer_transactions ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_transactions_client_request_id ON customer_transactions(client_request_id);
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_client_request_id ON expenses(client_request_id);
