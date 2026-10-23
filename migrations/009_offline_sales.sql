-- Stable request IDs let mobile clients safely retry a sale after a lost reply.
ALTER TABLE sales ADD COLUMN IF NOT EXISTS client_request_id VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_client_request_id
    ON sales (client_request_id);
