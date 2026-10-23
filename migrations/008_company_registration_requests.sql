-- Company signup applications stay pending until a developer approves them.
CREATE TABLE IF NOT EXISTS company_registration_requests (
    id SERIAL PRIMARY KEY,
    company_name VARCHAR(150) NOT NULL,
    address TEXT NOT NULL,
    branch_count INTEGER NOT NULL CHECK (branch_count BETWEEN 1 AND 500),
    phone VARCHAR(30) NOT NULL,
    email VARCHAR(150) NOT NULL,
    message TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_by INTEGER REFERENCES users(id),
    reviewed_by INTEGER REFERENCES users(id),
    company_id INTEGER REFERENCES companies(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_company_registration_requests_status_created
    ON company_registration_requests(status, created_at DESC);
