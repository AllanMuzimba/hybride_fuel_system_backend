BEGIN;

CREATE TABLE IF NOT EXISTS user_company_memberships (
    id SERIAL PRIMARY KEY,
    user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    branch_id INT REFERENCES company_branches(id) ON DELETE SET NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, company_id)
);

CREATE INDEX IF NOT EXISTS idx_user_company_memberships_user
    ON user_company_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_user_company_memberships_company
    ON user_company_memberships(company_id);

-- Preserve each existing account's current company and branch as its first
-- membership, so existing logins keep their current access after upgrade.
INSERT INTO user_company_memberships (user_id, company_id, branch_id)
SELECT id, company_id, branch_id FROM users
ON CONFLICT (user_id, company_id) DO NOTHING;

DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fuel_user') THEN
    GRANT ALL PRIVILEGES ON TABLE user_company_memberships TO fuel_user;
    GRANT ALL PRIVILEGES ON SEQUENCE user_company_memberships_id_seq TO fuel_user;
  END IF;
END $$;

COMMIT;
