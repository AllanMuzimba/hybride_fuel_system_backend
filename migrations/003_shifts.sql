-- ===========================================================================
-- Migration 003 — shift management (idempotent; safe to re-run)
--   * shifts              -- one operating period at a branch (open/closed)
--   * shift_attendance    -- who signed in for that shift (attendant log)
--   * sales.shift_id      -- every sale is booked to the shift it happened in
--   * sales.entry_source  -- 'realtime' (recorded live) |
--                            'shift_close' (volume entered at end of shift)
--
-- A branch may have at most ONE open shift at a time (partial unique index).
-- Apply with:  psql -d fuel_system -f migrations/003_shifts.sql
-- ===========================================================================

CREATE TABLE IF NOT EXISTS shifts (
    id              SERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    branch_id       INT NOT NULL REFERENCES company_branches(id),
    shift_date      DATE NOT NULL DEFAULT CURRENT_DATE,
    shift_label     VARCHAR(60) NOT NULL DEFAULT 'Custom',  -- 'Morning', 'Night', ...
    start_time      TIME NOT NULL,
    end_time        TIME NOT NULL,
    -- realtime     -> attendants record each sale as it happens
    -- end_of_shift -> a manager/supervisor enters the litres sold afterwards
    entry_mode      VARCHAR(20) NOT NULL DEFAULT 'realtime'
                    CHECK (entry_mode IN ('realtime', 'end_of_shift')),
    status          VARCHAR(10) NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'closed')),
    opened_by       INT NOT NULL REFERENCES users(id),
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_by       INT REFERENCES users(id),
    closed_at       TIMESTAMPTZ,
    notes           TEXT,
    CONSTRAINT chk_shift_period CHECK (end_time <> start_time)
);

-- One open shift per branch at a time.
CREATE UNIQUE INDEX IF NOT EXISTS uq_shifts_open_branch
    ON shifts(branch_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_shifts_company  ON shifts(company_id);
CREATE INDEX IF NOT EXISTS idx_shifts_branch   ON shifts(branch_id, shift_date DESC);

CREATE TABLE IF NOT EXISTS shift_attendance (
    id          SERIAL PRIMARY KEY,
    shift_id    INT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    user_id     INT NOT NULL REFERENCES users(id),
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    left_at     TIMESTAMPTZ,
    UNIQUE (shift_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_shift_att_user ON shift_attendance(user_id);

-- ----------------------------------------------------------------- sales ----
ALTER TABLE sales ADD COLUMN IF NOT EXISTS shift_id INT REFERENCES shifts(id);
ALTER TABLE sales ADD COLUMN IF NOT EXISTS entry_source VARCHAR(20) NOT NULL DEFAULT 'realtime';
CREATE INDEX IF NOT EXISTS idx_sales_shift ON sales(shift_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'chk_sales_entry_source'
    ) THEN
        ALTER TABLE sales
            ADD CONSTRAINT chk_sales_entry_source
            CHECK (entry_source IN ('realtime', 'shift_close'));
    END IF;
END $$;

-- ----------------------------------------------------------------- grants ---
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'fuel_user') THEN
        GRANT ALL ON SCHEMA public TO fuel_user;
        GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO fuel_user;
        GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO fuel_user;
        GRANT EXECUTE ON ALL FUNCTIONS        IN SCHEMA public TO fuel_user;
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO fuel_user';
        EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO fuel_user';
    END IF;
END $$;
