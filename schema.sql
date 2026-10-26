-- =====================================================================
-- FUEL MANAGEMENT SYSTEM — PostgreSQL Schema (multi-tenant)
-- =====================================================================
-- Run as: psql -U <user> -d <database> -f schema.sql
-- Requires PostgreSQL 13+ (generated columns, ON CONFLICT).
--
-- TENANCY MODEL
--   companies          -- top-level tenant (a fuel business)
--   company_branches   -- each company's outlets/stations
--   users, suppliers, fuel_types, customers, expense_categories
--                       -- scoped to a company (shared across its branches)
--   fuel_stock, fuel_receipts, sales, customer_transactions, expenses,
--   daily_fuel_reports, daily_financial_summary, shifts, shift_attendance
--                       -- scoped to a branch (each branch runs its own
--                          stock/till; company-wide totals are obtained by
--                          joining through company_branches)
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1. ENUM TYPES
-- ---------------------------------------------------------------------
CREATE TYPE user_role       AS ENUM ('attendant', 'supervisor', 'manager', 'director', 'developer', 'dev_support', 'secretary', 'cashier');
CREATE TYPE payment_status  AS ENUM ('unpaid', 'partial', 'paid');
CREATE TYPE payment_method  AS ENUM ('cash', 'card', 'mobile_money', 'account');
CREATE TYPE customer_txn_type AS ENUM ('fill_up', 'payment', 'adjustment');

-- ---------------------------------------------------------------------
-- 2. COMPANIES  (tenants)
-- ---------------------------------------------------------------------
CREATE TABLE companies (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(150) NOT NULL,
    address         TEXT,
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    phone           VARCHAR(30),
    email           VARCHAR(150),
    slug            VARCHAR(100) UNIQUE NOT NULL,     -- e.g. for a subdomain or public URL
    url             VARCHAR(255),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    -- registered_by/confirmed_by point at users(id); the FKs are declared
    -- right after the users table below (companies <-> users reference each
    -- other, so PostgreSQL will not accept them inline here).
    registered_by   INT,
    confirmed_by    INT,
    confirmed_at    TIMESTAMPTZ,
    needs_confirmation BOOLEAN   NOT NULL DEFAULT FALSE,   -- dev_support sign-ups wait for a developer
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 3. COMPANY BRANCHES  (each company manages its own outlets)
-- ---------------------------------------------------------------------
CREATE TABLE company_branches (
    id              SERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    branch_name     VARCHAR(150) NOT NULL,
    phone           VARCHAR(30),
    address         TEXT,
    latitude        NUMERIC(10,7),
    longitude       NUMERIC(10,7),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE(company_id, branch_name)
);
CREATE INDEX idx_branches_company ON company_branches(company_id);

-- ---------------------------------------------------------------------
-- 4. USERS  (attendant / supervisor / manager / director / developer)
--    branch_id NULL = access across every branch of their company
--    (typically director/developer); attendants/supervisors are usually
--    tied to the one branch they work at.
-- ---------------------------------------------------------------------
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    branch_id       INT REFERENCES company_branches(id),
    username        VARCHAR(50)  UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    full_name       VARCHAR(150) NOT NULL,
    role            user_role    NOT NULL DEFAULT 'attendant',
    phone           VARCHAR(30),
    email           VARCHAR(150),
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_users_company ON users(company_id);
CREATE INDEX idx_users_branch  ON users(branch_id);
CREATE UNIQUE INDEX uq_users_email_ci
    ON users(lower(btrim(email))) WHERE email IS NOT NULL AND btrim(email) <> '';
CREATE UNIQUE INDEX uq_users_phone_digits
    ON users(regexp_replace(phone, '[^0-9]', '', 'g'))
    WHERE phone IS NOT NULL AND regexp_replace(phone, '[^0-9]', '', 'g') <> '';

CREATE TABLE user_company_memberships (
    id              SERIAL PRIMARY KEY,
    user_id         INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    company_id      INT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    branch_id       INT REFERENCES company_branches(id) ON DELETE SET NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, company_id)
);
CREATE INDEX idx_user_company_memberships_user ON user_company_memberships(user_id);
CREATE INDEX idx_user_company_memberships_company ON user_company_memberships(company_id);
INSERT INTO user_company_memberships (user_id, company_id, branch_id)
SELECT id, company_id, branch_id FROM users;

-- companies.registered_by / confirmed_by -> users(id). Declared here rather
-- than inside the companies table above because the two tables reference each
-- other and PostgreSQL only accepts foreign keys to tables that already exist.
ALTER TABLE companies
    ADD CONSTRAINT fk_companies_registered_by FOREIGN KEY (registered_by) REFERENCES users(id);
ALTER TABLE companies
    ADD CONSTRAINT fk_companies_confirmed_by  FOREIGN KEY (confirmed_by)  REFERENCES users(id);

CREATE TABLE password_reset_tokens (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_password_reset_tokens_user ON password_reset_tokens(user_id);

CREATE TABLE company_invoices (
    id SERIAL PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(id),
    invoice_number VARCHAR(50) NOT NULL UNIQUE,
    description VARCHAR(255) NOT NULL,
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    status VARCHAR(20) NOT NULL DEFAULT 'unpaid'
        CHECK (status IN ('unpaid', 'paid', 'void')),
    issued_at DATE NOT NULL,
    due_at DATE NOT NULL,
    notes TEXT,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_company_invoices_company_issued
    ON company_invoices(company_id, issued_at DESC);

CREATE TABLE company_registration_requests (
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
CREATE INDEX idx_company_registration_requests_status_created
    ON company_registration_requests(status, created_at DESC);

-- ---------------------------------------------------------------------
-- 5. SUPPLIERS  (shared across all of a company's branches)
-- ---------------------------------------------------------------------
CREATE TABLE suppliers (
    id              SERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    name            VARCHAR(150) NOT NULL,
    contact_person  VARCHAR(150),
    phone           VARCHAR(30),
    email           VARCHAR(150),
    address         TEXT,
    vat_number      VARCHAR(50),           -- VAT / tax number, optional
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX idx_suppliers_company ON suppliers(company_id);

-- ---------------------------------------------------------------------
-- 6. FUEL TYPES  (company-level identity + HQ default pricing).
--    Actual selling happens at a branch's OWN price — see
--    branch_fuel_prices below. default_cost/default_sell_price are HQ's
--    reference price and the starting point new branches are seeded with.
-- ---------------------------------------------------------------------
CREATE TABLE fuel_types (
    id                  SERIAL PRIMARY KEY,
    company_id          INT NOT NULL REFERENCES companies(id),
    name                VARCHAR(50)  NOT NULL,             -- e.g. Diesel, Petrol 93, Petrol 95
    default_cost        NUMERIC(12,2) NOT NULL DEFAULT 0,  -- HQ reference buying cost per litre
    default_sell_price  NUMERIC(12,2) NOT NULL DEFAULT 0,  -- HQ reference selling price per litre
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by          INT REFERENCES users(id),
    UNIQUE(company_id, name)
);

-- Per-branch pricing — this is the price actually used when that branch
-- sells fuel. Seeded automatically from the fuel type's (or branch's)
-- defaults the moment either is created (see triggers below), and each
-- branch can then move its own price independently of HQ's default and
-- of every other branch.
CREATE TABLE branch_fuel_prices (
    branch_id       INT NOT NULL REFERENCES company_branches(id),
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id),
    current_cost    NUMERIC(12,2) NOT NULL DEFAULT 0,
    sell_price      NUMERIC(12,2) NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      INT REFERENCES users(id),
    PRIMARY KEY (branch_id, fuel_type_id)
);

-- Live stock — one row per branch per fuel type (each branch's own tank/pump volume)
CREATE TABLE fuel_stock (
    branch_id       INT NOT NULL REFERENCES company_branches(id),
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id),
    current_volume  NUMERIC(14,3) NOT NULL DEFAULT 0,
    capacity        NUMERIC(14,3),   -- tank/storage capacity for this branch+fuel; NULL = not tracked
    tank_height_cm  NUMERIC(8,2) NOT NULL DEFAULT 100,
                                       -- calibrated dip-stick height (cm) of this tank; used to
                                       -- turn a stick reading into litres when no dip chart exists
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (branch_id, fuel_type_id)
);

-- ---------------------------------------------------------------------
-- 6b. TANK DIP CALIBRATION — per branch + fuel type, a dip chart of
--     stick reading (cm from the bottom) -> litres. fn_dip_to_volume()
--     interpolates between these points; a branch that has never entered
--     a chart is handled by a straight line from tank_height_cm (empty) to
--     capacity (full), so dip readings work out of the box.
-- ---------------------------------------------------------------------
CREATE TABLE tank_dip_calibrations (
    id              SERIAL PRIMARY KEY,
    branch_id       INT NOT NULL REFERENCES company_branches(id) ON DELETE CASCADE,
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id) ON DELETE CASCADE,
    dip_cm          NUMERIC(8,2)  NOT NULL CHECK (dip_cm >= 0),
    volume_litres   NUMERIC(14,3) NOT NULL CHECK (volume_litres >= 0),
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (branch_id, fuel_type_id, dip_cm)
);
CREATE INDEX idx_dip_cal_branch ON tank_dip_calibrations(branch_id, fuel_type_id);

-- ---------------------------------------------------------------------
-- 7. FUEL RECEIPTS  ("receive" table — stock coming in from suppliers,
--    always into a specific branch)
-- ---------------------------------------------------------------------
CREATE TABLE fuel_receipts (
    id                  SERIAL PRIMARY KEY,
    client_request_id   VARCHAR(64) UNIQUE,
    branch_id           INT NOT NULL REFERENCES company_branches(id),
    supplier_id         INT NOT NULL REFERENCES suppliers(id),
    fuel_type_id        INT NOT NULL REFERENCES fuel_types(id),
    volume              NUMERIC(14,3) NOT NULL CHECK (volume > 0),
    cost_per_liter      NUMERIC(12,2) NOT NULL CHECK (cost_per_liter >= 0),
    total_cost          NUMERIC(14,2) GENERATED ALWAYS AS (volume * cost_per_liter) STORED,
    invoice_reference   VARCHAR(100) NOT NULL,          -- supplier invoice number
    payment_status      payment_status NOT NULL DEFAULT 'unpaid',
    amount_paid         NUMERIC(14,2) NOT NULL DEFAULT 0,
    received_date       DATE NOT NULL DEFAULT CURRENT_DATE,
    received_by         INT NOT NULL REFERENCES users(id),
    dip_before_cm       NUMERIC(8,2),      -- dip-stick reading before the delivery (cm)
    dip_after_cm        NUMERIC(8,2),      -- dip-stick reading after the delivery (cm)
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_receipts_branch   ON fuel_receipts(branch_id);
CREATE INDEX idx_receipts_date     ON fuel_receipts(received_date);
CREATE INDEX idx_receipts_supplier ON fuel_receipts(supplier_id);
CREATE INDEX idx_receipts_invoice  ON fuel_receipts(invoice_reference);

-- Payments made against a supplier invoice (a receipt can be paid in parts)
CREATE TABLE supplier_payments (
    id              SERIAL PRIMARY KEY,
    client_request_id VARCHAR(64) UNIQUE,
    receipt_id      INT NOT NULL REFERENCES fuel_receipts(id),
    amount          NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    payment_date    DATE NOT NULL DEFAULT CURRENT_DATE,
    paid_by         INT NOT NULL REFERENCES users(id),
    notes           TEXT,
    reference       VARCHAR(100),  -- bank transfer / EFT / cheque reference
    payment_channel VARCHAR(20) NOT NULL DEFAULT 'unknown'
                    CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_supplier_payments_receipt ON supplier_payments(receipt_id);

-- ---------------------------------------------------------------------
-- 8. CUSTOMERS  (credit / account customers — company-wide, so one
--    customer can fill up at any of the company's branches)
-- ---------------------------------------------------------------------
CREATE TABLE customers (
    id              SERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    company_name    VARCHAR(150),      -- the CUSTOMER's own business name (unrelated to tenant `companies`)
    name            VARCHAR(150) NOT NULL,
    phone           VARCHAR(30),
    address         TEXT,
    email           VARCHAR(150),
    monthly_limit   NUMERIC(14,2) NOT NULL DEFAULT 0,   -- 0 = no credit limit enforced
    current_balance NUMERIC(14,2) NOT NULL DEFAULT 0,   -- positive = customer owes the business
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    registered_by   INT REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_customers_company ON customers(company_id);

-- ---------------------------------------------------------------------
-- 8b. SHIFTS + ATTENDANCE  (a branch runs its forecourt in shifts: the
--     shift carries the date/period, everyone who signs in is recorded,
--     and every sale is booked against it. One open shift per branch.)
-- ---------------------------------------------------------------------
CREATE TABLE shift_templates (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    name VARCHAR(60) NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (start_time <> end_time),
    UNIQUE (company_id, name)
);

CREATE TABLE shifts (
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
    system_cash_amount NUMERIC(14,2),
    counted_cash_amount NUMERIC(14,2),
    CONSTRAINT chk_shift_period CHECK (end_time <> start_time)
);
-- One open shift per branch at a time.
CREATE UNIQUE INDEX uq_shifts_open_branch ON shifts(branch_id) WHERE status = 'open';
CREATE INDEX idx_shifts_company ON shifts(company_id);
CREATE INDEX idx_shifts_branch  ON shifts(branch_id, shift_date DESC);

CREATE TABLE shift_fuel_readings (
    id SERIAL PRIMARY KEY,
    shift_id INT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    fuel_type_id INT NOT NULL REFERENCES fuel_types(id),
    opening_system_volume NUMERIC(14,3) NOT NULL DEFAULT 0,
    opening_manual_volume NUMERIC(14,3),
    opening_dip_cm NUMERIC(8,2),
    closing_system_volume NUMERIC(14,3),
    closing_manual_volume NUMERIC(14,3),
    closing_dip_cm NUMERIC(8,2),
    UNIQUE (shift_id, fuel_type_id)
);
CREATE INDEX idx_shift_fuel_readings_shift ON shift_fuel_readings(shift_id);

-- Who signed in for the shift: attendance details kept per person so the
-- shift page can show exactly who was on the forecourt and when.
CREATE TABLE shift_attendance (
    id          SERIAL PRIMARY KEY,
    shift_id    INT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    user_id     INT NOT NULL REFERENCES users(id),
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    left_at     TIMESTAMPTZ,
    system_cash_amount NUMERIC(14,2),
    counted_cash_amount NUMERIC(14,2),
    UNIQUE (shift_id, user_id)
);
CREATE INDEX idx_shift_att_user ON shift_attendance(user_id);

-- ---------------------------------------------------------------------
-- 9. SALES  ("selling" table — also drives stock, always at one branch)
-- ---------------------------------------------------------------------
CREATE TABLE sales (
    id              SERIAL PRIMARY KEY,
    client_request_id VARCHAR(64) UNIQUE,
    branch_id       INT NOT NULL REFERENCES company_branches(id),
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id),
    customer_id     INT REFERENCES customers(id),        -- NULL = walk-in cash sale
    shift_id        INT REFERENCES shifts(id),           -- shift the sale was booked to
    volume          NUMERIC(14,3) NOT NULL CHECK (volume > 0),
    cost_price      NUMERIC(12,2) NOT NULL,               -- snapshot of fuel_types.current_cost
    sell_price      NUMERIC(12,2) NOT NULL,               -- snapshot of fuel_types.sell_price
    total_amount    NUMERIC(14,2) GENERATED ALWAYS AS (volume * sell_price) STORED,
    payment_method  payment_method NOT NULL,
    served_by       INT NOT NULL REFERENCES users(id),
    sale_date       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- 'realtime' = recorded live | 'shift_close' = litres entered at end of shift
    entry_source    VARCHAR(20) NOT NULL DEFAULT 'realtime'
                    CHECK (entry_source IN ('realtime', 'shift_close')),
    notes           TEXT,
    CONSTRAINT chk_account_needs_customer
        CHECK (payment_method <> 'account' OR customer_id IS NOT NULL)
);
CREATE INDEX idx_sales_branch    ON sales(branch_id);
CREATE INDEX idx_sales_date      ON sales(sale_date);
CREATE INDEX idx_sales_customer  ON sales(customer_id);
CREATE INDEX idx_sales_fuel_type ON sales(fuel_type_id);
CREATE INDEX idx_sales_shift     ON sales(shift_id);

-- ---------------------------------------------------------------------
-- 10. CUSTOMER TRANSACTIONS  (fill_up / payment / adjustment history)
-- ---------------------------------------------------------------------
-- Sign convention: fill_up = +amount (increases what they owe)
--                  payment = -amount (reduces what they owe)
--                  adjustment = +/- as needed
CREATE TABLE customer_transactions (
    id                  SERIAL PRIMARY KEY,
    client_request_id   VARCHAR(64) UNIQUE,
    branch_id           INT NOT NULL REFERENCES company_branches(id),  -- branch where it happened
    customer_id         INT NOT NULL REFERENCES customers(id),
    txn_type            customer_txn_type NOT NULL,
    sale_id             INT REFERENCES sales(id),
    amount              NUMERIC(14,2) NOT NULL,
    balance_after       NUMERIC(14,2) NOT NULL,
    recorded_by         INT NOT NULL REFERENCES users(id),
    transaction_date    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes               TEXT,
    payment_channel     VARCHAR(20) NOT NULL DEFAULT 'unknown'
                        CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown'))
);
CREATE INDEX idx_cust_txn_branch   ON customer_transactions(branch_id);
CREATE INDEX idx_cust_txn_date     ON customer_transactions(transaction_date);
CREATE INDEX idx_cust_txn_customer ON customer_transactions(customer_id);

-- ---------------------------------------------------------------------
-- 11. EXPENSES  (categories per company; an expense entry can be tied to
--     one branch's till, or left company-wide for head-office costs)
-- ---------------------------------------------------------------------
CREATE TABLE expense_categories (
    id          SERIAL PRIMARY KEY,
    company_id  INT NOT NULL REFERENCES companies(id),
    name        VARCHAR(100) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(company_id, name)
);

CREATE TABLE expenses (
    id                  SERIAL PRIMARY KEY,
    client_request_id   VARCHAR(64) UNIQUE,
    company_id          INT NOT NULL REFERENCES companies(id),
    branch_id           INT REFERENCES company_branches(id),  -- NULL = company-wide / head-office expense
    name                VARCHAR(150) NOT NULL,
    category_id         INT NOT NULL REFERENCES expense_categories(id),
    amount              NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    reference           VARCHAR(100),                          -- e.g. invoice/receipt number
    notes               TEXT,
    receipt_id          INT REFERENCES fuel_receipts(id),      -- supplier invoice for payment expenses
    supplier_payment_id INT UNIQUE REFERENCES supplier_payments(id),
    expense_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    recorded_by         INT REFERENCES users(id),               -- NULL only for system-generated rows
    is_system_generated BOOLEAN NOT NULL DEFAULT FALSE,
    payment_channel VARCHAR(20) NOT NULL DEFAULT 'unknown'
                    CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_expenses_company  ON expenses(company_id);
CREATE INDEX idx_expenses_branch   ON expenses(branch_id);
CREATE INDEX idx_expenses_date     ON expenses(expense_date);
CREATE INDEX idx_expenses_category ON expenses(category_id);
CREATE INDEX idx_expenses_receipt ON expenses(receipt_id) WHERE receipt_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 12. DAILY FUEL REPORTS  (volume + monetary, one row per day per branch
--     per fuel type). sold_volume/total_sales_amount = ALL sales (cash +
--     credit — the fuel physically left either way). credit_volume/
--     credit_amount is the subset given to customers on account (not yet
--     cash income).
-- ---------------------------------------------------------------------
CREATE TABLE daily_fuel_reports (
    id                      SERIAL PRIMARY KEY,
    branch_id               INT NOT NULL REFERENCES company_branches(id),
    report_date             DATE NOT NULL,
    fuel_type_id            INT NOT NULL REFERENCES fuel_types(id),
    opening_stock           NUMERIC(14,3) NOT NULL DEFAULT 0,
    received_volume         NUMERIC(14,3) NOT NULL DEFAULT 0,
    sold_volume             NUMERIC(14,3) NOT NULL DEFAULT 0,
    credit_volume           NUMERIC(14,3) NOT NULL DEFAULT 0,
    closing_stock           NUMERIC(14,3) NOT NULL DEFAULT 0,
    total_purchase_amount   NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_sales_amount      NUMERIC(14,2) NOT NULL DEFAULT 0,
    credit_amount           NUMERIC(14,2) NOT NULL DEFAULT 0,
    generated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(branch_id, report_date, fuel_type_id)
);
CREATE INDEX idx_daily_reports_date   ON daily_fuel_reports(report_date);
CREATE INDEX idx_daily_reports_branch ON daily_fuel_reports(branch_id);

-- ---------------------------------------------------------------------
-- 13. DAILY FINANCIAL SUMMARY  (one row per day per branch — the money
--     side). income = cash/card/mobile-money sales THAT DAY + any debt
--     customers paid off THAT DAY (cash-basis: credit sales themselves
--     are not income until collected).
-- ---------------------------------------------------------------------
CREATE TABLE daily_financial_summary (
    id                          SERIAL PRIMARY KEY,
    branch_id                   INT NOT NULL REFERENCES company_branches(id),
    summary_date                DATE NOT NULL,
    opening_balance             NUMERIC(14,2) NOT NULL DEFAULT 0,
    cash_sales_income           NUMERIC(14,2) NOT NULL DEFAULT 0,  -- cash/card/mobile_money sales
    customer_payments_income    NUMERIC(14,2) NOT NULL DEFAULT 0,  -- debt collected from customers
    total_income                NUMERIC(14,2) NOT NULL DEFAULT 0,  -- the two above, combined
    total_expenses              NUMERIC(14,2) NOT NULL DEFAULT 0,
    closing_balance             NUMERIC(14,2) NOT NULL DEFAULT 0,  -- opening + income - expenses
    generated_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(branch_id, summary_date)
);
CREATE INDEX idx_financial_summary_date   ON daily_financial_summary(summary_date);
CREATE INDEX idx_financial_summary_branch ON daily_financial_summary(branch_id);

-- ---------------------------------------------------------------------
-- 14. ACTIVITY LOG  (append-only audit trail — HQ's window into what
--     happened at every branch: sales, receipts, payments, expenses,
--     price changes, ... one row per event, who did it and where)
-- ---------------------------------------------------------------------
CREATE TABLE activity_log (
    id              BIGSERIAL PRIMARY KEY,
    company_id      INT NOT NULL REFERENCES companies(id),
    branch_id       INT REFERENCES company_branches(id),   -- NULL for company-wide actions
    user_id         INT REFERENCES users(id),
    activity_type   VARCHAR(50) NOT NULL,   -- 'sale' | 'receipt' | 'supplier_payment' |
                                             -- 'customer_payment' | 'expense' | 'price_change'
    description     TEXT,
    amount          NUMERIC(14,2),
    reference_table VARCHAR(50),
    reference_id    INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_activity_log_company ON activity_log(company_id);
CREATE INDEX idx_activity_log_branch  ON activity_log(branch_id);
CREATE INDEX idx_activity_log_date    ON activity_log(created_at);
CREATE INDEX idx_activity_log_type    ON activity_log(activity_type);

COMMIT;

-- =====================================================================
-- FUNCTIONS + TRIGGERS
-- =====================================================================

-- ---------------------------------------------------------------------
-- New company -> seed its default expense category so paid invoices have
-- somewhere to post their auto-generated expense.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_company_after_insert()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO expense_categories (company_id, name) VALUES (NEW.id, 'Fuel Purchases');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_company_insert
AFTER INSERT ON companies
FOR EACH ROW EXECUTE FUNCTION trg_fn_company_after_insert();

-- Convenience wrapper for the API's company signup endpoint — a plain
-- INSERT into companies would trigger the same seeding via the trigger
-- above, this just gives the API a single call that returns the new id.
CREATE OR REPLACE FUNCTION fn_create_company(
    p_name VARCHAR, p_slug VARCHAR, p_address TEXT, p_lat NUMERIC, p_lng NUMERIC,
    p_phone VARCHAR, p_email VARCHAR, p_url VARCHAR
) RETURNS INT AS $$
DECLARE
    v_id INT;
BEGIN
    INSERT INTO companies (name, slug, address, latitude, longitude, phone, email, url)
    VALUES (p_name, p_slug, p_address, p_lat, p_lng, p_phone, p_email, p_url)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- Activity log helper — every trigger below that records a business event
-- (a sale, a receipt, a payment, an expense, a price change) calls this
-- once, so HQ has one append-only, filterable trail of "who did what,
-- where, when" to audit.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_log_activity(
    p_company_id INT, p_branch_id INT, p_user_id INT, p_activity_type VARCHAR,
    p_description TEXT, p_amount NUMERIC, p_reference_table VARCHAR, p_reference_id INT
) RETURNS VOID AS $$
BEGIN
    INSERT INTO activity_log (
        company_id, branch_id, user_id, activity_type, description, amount, reference_table, reference_id
    ) VALUES (
        p_company_id, p_branch_id, p_user_id, p_activity_type, p_description, p_amount, p_reference_table, p_reference_id
    );
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- Per-branch pricing seeding: the moment a fuel type is defined (HQ sets
-- its default price) or a new branch is opened, every existing
-- branch/fuel-type combination for that company gets a starting
-- branch_fuel_prices row copied from the fuel type's defaults. From then
-- on each branch's price moves independently (see PATCH .../branch-prices).
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_fueltype_after_insert_seed_prices()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO branch_fuel_prices (branch_id, fuel_type_id, current_cost, sell_price, updated_by)
    SELECT cb.id, NEW.id, NEW.default_cost, NEW.default_sell_price, NEW.updated_by
    FROM company_branches cb
    WHERE cb.company_id = NEW.company_id
    ON CONFLICT (branch_id, fuel_type_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_fueltype_insert_seed_prices
AFTER INSERT ON fuel_types
FOR EACH ROW EXECUTE FUNCTION trg_fn_fueltype_after_insert_seed_prices();

CREATE OR REPLACE FUNCTION trg_fn_branch_after_insert_seed_prices()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO branch_fuel_prices (branch_id, fuel_type_id, current_cost, sell_price)
    SELECT NEW.id, ft.id, ft.default_cost, ft.default_sell_price
    FROM fuel_types ft
    WHERE ft.company_id = NEW.company_id
    ON CONFLICT (branch_id, fuel_type_id) DO NOTHING;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_branch_insert_seed_prices
AFTER INSERT ON company_branches
FOR EACH ROW EXECUTE FUNCTION trg_fn_branch_after_insert_seed_prices();

-- Log a price change (skip the automatic seeding insert above — only log
-- when a branch's price is actually deliberately updated afterwards).
CREATE OR REPLACE FUNCTION trg_fn_branch_price_after_update()
RETURNS TRIGGER AS $$
DECLARE
    v_company_id INT;
    v_fuel_name  VARCHAR(50);
BEGIN
    IF NEW.current_cost IS DISTINCT FROM OLD.current_cost OR NEW.sell_price IS DISTINCT FROM OLD.sell_price THEN
        SELECT company_id INTO v_company_id FROM company_branches WHERE id = NEW.branch_id;
        SELECT name INTO v_fuel_name FROM fuel_types WHERE id = NEW.fuel_type_id;

        PERFORM fn_log_activity(
            v_company_id, NEW.branch_id, NEW.updated_by, 'price_change',
            format('%s price changed: cost %s -> %s, sell %s -> %s',
                   COALESCE(v_fuel_name, 'Fuel'), OLD.current_cost, NEW.current_cost, OLD.sell_price, NEW.sell_price),
            NEW.sell_price, 'branch_fuel_prices', NEW.fuel_type_id
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_branch_price_update
AFTER UPDATE ON branch_fuel_prices
FOR EACH ROW EXECUTE FUNCTION trg_fn_branch_price_after_update();

-- ---------------------------------------------------------------------
-- Recompute the daily fuel report row for one (branch, date, fuel_type)
-- from source data. Idempotent — safe to call as many times as you like,
-- including for backdated corrections.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_refresh_daily_report(p_branch_id INT, p_date DATE, p_fuel_type_id INT)
RETURNS VOID AS $$
DECLARE
    v_prev_closing  NUMERIC(14,3);
    v_opening       NUMERIC(14,3);
    v_received      NUMERIC(14,3);
    v_sold          NUMERIC(14,3);
    v_purchase_amt  NUMERIC(14,2);
    v_sales_amt     NUMERIC(14,2);
    v_credit_vol    NUMERIC(14,3);
    v_credit_amt    NUMERIC(14,2);
BEGIN
    SELECT closing_stock INTO v_prev_closing
    FROM daily_fuel_reports
    WHERE branch_id = p_branch_id AND fuel_type_id = p_fuel_type_id AND report_date < p_date
    ORDER BY report_date DESC
    LIMIT 1;

    v_opening := COALESCE(v_prev_closing, 0);

    SELECT COALESCE(SUM(volume), 0), COALESCE(SUM(total_cost), 0)
      INTO v_received, v_purchase_amt
      FROM fuel_receipts
      WHERE branch_id = p_branch_id AND received_date = p_date AND fuel_type_id = p_fuel_type_id;

    SELECT COALESCE(SUM(volume), 0), COALESCE(SUM(total_amount), 0)
      INTO v_sold, v_sales_amt
      FROM sales
      WHERE branch_id = p_branch_id AND sale_date::date = p_date AND fuel_type_id = p_fuel_type_id;

    -- portion of the above given to customers on credit (account sales) —
    -- still physical fuel out the door, but not yet cash income
    SELECT COALESCE(SUM(volume), 0), COALESCE(SUM(total_amount), 0)
      INTO v_credit_vol, v_credit_amt
      FROM sales
      WHERE branch_id = p_branch_id AND sale_date::date = p_date AND fuel_type_id = p_fuel_type_id
        AND payment_method = 'account';

    INSERT INTO daily_fuel_reports (
        branch_id, report_date, fuel_type_id, opening_stock, received_volume,
        sold_volume, credit_volume, closing_stock,
        total_purchase_amount, total_sales_amount, credit_amount, generated_at
    )
    VALUES (
        p_branch_id, p_date, p_fuel_type_id, v_opening, v_received,
        v_sold, v_credit_vol, v_opening + v_received - v_sold,
        v_purchase_amt, v_sales_amt, v_credit_amt, now()
    )
    ON CONFLICT (branch_id, report_date, fuel_type_id) DO UPDATE SET
        opening_stock         = EXCLUDED.opening_stock,
        received_volume       = EXCLUDED.received_volume,
        sold_volume           = EXCLUDED.sold_volume,
        credit_volume         = EXCLUDED.credit_volume,
        closing_stock         = EXCLUDED.closing_stock,
        total_purchase_amount = EXCLUDED.total_purchase_amount,
        total_sales_amount    = EXCLUDED.total_sales_amount,
        credit_amount         = EXCLUDED.credit_amount,
        generated_at          = now();
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- Recompute the financial summary row for one (branch, date) from source
-- data: expenses, cash/card/mobile sales, and debt collected from
-- customers. Idempotent, same pattern as fn_refresh_daily_report.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_refresh_financial_summary(p_branch_id INT, p_date DATE)
RETURNS VOID AS $$
DECLARE
    v_prev_closing  NUMERIC(14,2);
    v_opening       NUMERIC(14,2);
    v_cash_sales    NUMERIC(14,2);
    v_cust_payments NUMERIC(14,2);
    v_expenses      NUMERIC(14,2);
BEGIN
    SELECT closing_balance INTO v_prev_closing
    FROM daily_financial_summary
    WHERE branch_id = p_branch_id AND summary_date < p_date
    ORDER BY summary_date DESC
    LIMIT 1;

    v_opening := COALESCE(v_prev_closing, 0);

    SELECT COALESCE(SUM(total_amount), 0) INTO v_cash_sales
    FROM sales
    WHERE branch_id = p_branch_id AND sale_date::date = p_date AND payment_method <> 'account';

    -- customer_transactions.amount is negative for payments (sign convention
    -- documented on that table), so flip it back to a positive cash inflow
    SELECT COALESCE(SUM(-amount), 0) INTO v_cust_payments
    FROM customer_transactions
    WHERE branch_id = p_branch_id AND transaction_date::date = p_date AND txn_type = 'payment';

    SELECT COALESCE(SUM(amount), 0) INTO v_expenses
    FROM expenses
    WHERE branch_id = p_branch_id AND expense_date = p_date;

    INSERT INTO daily_financial_summary (
        branch_id, summary_date, opening_balance, cash_sales_income, customer_payments_income,
        total_income, total_expenses, closing_balance, generated_at
    )
    VALUES (
        p_branch_id, p_date, v_opening, v_cash_sales, v_cust_payments,
        v_cash_sales + v_cust_payments, v_expenses,
        v_opening + v_cash_sales + v_cust_payments - v_expenses, now()
    )
    ON CONFLICT (branch_id, summary_date) DO UPDATE SET
        opening_balance          = EXCLUDED.opening_balance,
        cash_sales_income        = EXCLUDED.cash_sales_income,
        customer_payments_income = EXCLUDED.customer_payments_income,
        total_income             = EXCLUDED.total_income,
        total_expenses           = EXCLUDED.total_expenses,
        closing_balance          = EXCLUDED.closing_balance,
        generated_at             = now();
END;
$$ LANGUAGE plpgsql;

-- Recompute a whole date range, every branch and fuel type, in order (or
-- just one branch if p_branch_id is given). Use this once after loading
-- historical data, or after correcting a backdated receipt/sale/expense so
-- later days' running balances re-chain.
CREATE OR REPLACE FUNCTION fn_refresh_report_range(p_start DATE, p_end DATE, p_branch_id INT DEFAULT NULL)
RETURNS VOID AS $$
DECLARE
    d   DATE;
    br  RECORD;
    ft  RECORD;
BEGIN
    FOR d IN SELECT generate_series(p_start, p_end, '1 day')::date LOOP
        FOR br IN
            SELECT id, company_id FROM company_branches
            WHERE p_branch_id IS NULL OR id = p_branch_id
        LOOP
            FOR ft IN SELECT id FROM fuel_types WHERE company_id = br.company_id LOOP
                PERFORM fn_refresh_daily_report(br.id, d, ft.id);
            END LOOP;
            PERFORM fn_refresh_financial_summary(br.id, d);
        END LOOP;
    END LOOP;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- Trigger: before a fuel receipt is inserted -> supplier and fuel type
-- must belong to the same company as the receiving branch.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_check_receipt_company()
RETURNS TRIGGER AS $$
DECLARE
    v_branch_company    INT;
    v_supplier_company  INT;
    v_fueltype_company  INT;
BEGIN
    SELECT company_id INTO v_branch_company FROM company_branches WHERE id = NEW.branch_id;
    SELECT company_id INTO v_supplier_company FROM suppliers WHERE id = NEW.supplier_id;
    SELECT company_id INTO v_fueltype_company FROM fuel_types WHERE id = NEW.fuel_type_id;

    IF v_supplier_company IS DISTINCT FROM v_branch_company
       OR v_fueltype_company IS DISTINCT FROM v_branch_company THEN
        RAISE EXCEPTION 'Supplier and fuel type must belong to the same company as the receiving branch';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_before_receipt_check_company
BEFORE INSERT ON fuel_receipts
FOR EACH ROW EXECUTE FUNCTION trg_fn_check_receipt_company();

-- ---------------------------------------------------------------------
-- Trigger: before an expense is inserted -> its branch (if any) and its
-- category must belong to the same company as the expense itself.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_check_expense_company()
RETURNS TRIGGER AS $$
DECLARE
    v_branch_company    INT;
    v_category_company  INT;
BEGIN
    IF NEW.branch_id IS NOT NULL THEN
        SELECT company_id INTO v_branch_company FROM company_branches WHERE id = NEW.branch_id;
        IF v_branch_company IS DISTINCT FROM NEW.company_id THEN
            RAISE EXCEPTION 'Branch must belong to the same company as the expense';
        END IF;
    END IF;

    SELECT company_id INTO v_category_company FROM expense_categories WHERE id = NEW.category_id;
    IF v_category_company IS DISTINCT FROM NEW.company_id THEN
        RAISE EXCEPTION 'Expense category must belong to the same company as the expense';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_before_expense_check_company
BEFORE INSERT ON expenses
FOR EACH ROW EXECUTE FUNCTION trg_fn_check_expense_company();

-- ---------------------------------------------------------------------
-- Trigger: after a fuel receipt is inserted -> add to that branch's stock,
-- refresh its daily report
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_receipt_after_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_company_id INT;
BEGIN
    INSERT INTO fuel_stock(branch_id, fuel_type_id, current_volume, updated_at)
    VALUES (NEW.branch_id, NEW.fuel_type_id, NEW.volume, now())
    ON CONFLICT (branch_id, fuel_type_id) DO UPDATE
        SET current_volume = fuel_stock.current_volume + NEW.volume,
            updated_at = now();

    PERFORM fn_refresh_daily_report(NEW.branch_id, NEW.received_date, NEW.fuel_type_id);

    SELECT company_id INTO v_company_id FROM company_branches WHERE id = NEW.branch_id;
    PERFORM fn_log_activity(
        v_company_id, NEW.branch_id, NEW.received_by, 'receipt',
        format('Received %s L, invoice %s', NEW.volume, NEW.invoice_reference),
        NEW.total_cost, 'fuel_receipts', NEW.id
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_receipt_insert
AFTER INSERT ON fuel_receipts
FOR EACH ROW EXECUTE FUNCTION trg_fn_receipt_after_insert();

-- ---------------------------------------------------------------------
-- Trigger: before a sale is inserted -> fuel type and customer (if any)
-- must belong to the same company as the selling branch.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_check_sale_company()
RETURNS TRIGGER AS $$
DECLARE
    v_branch_company    INT;
    v_fueltype_company  INT;
    v_customer_company  INT;
BEGIN
    SELECT company_id INTO v_branch_company FROM company_branches WHERE id = NEW.branch_id;
    SELECT company_id INTO v_fueltype_company FROM fuel_types WHERE id = NEW.fuel_type_id;

    IF v_fueltype_company IS DISTINCT FROM v_branch_company THEN
        RAISE EXCEPTION 'Fuel type must belong to the same company as the selling branch';
    END IF;

    IF NEW.customer_id IS NOT NULL THEN
        SELECT company_id INTO v_customer_company FROM customers WHERE id = NEW.customer_id;
        IF v_customer_company IS DISTINCT FROM v_branch_company THEN
            RAISE EXCEPTION 'Customer must belong to the same company as the selling branch';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_before_sale_check_company
BEFORE INSERT ON sales
FOR EACH ROW EXECUTE FUNCTION trg_fn_check_sale_company();

-- ---------------------------------------------------------------------
-- Trigger: before a sale is inserted -> enforce customer credit limit
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_check_credit_limit()
RETURNS TRIGGER AS $$
DECLARE
    v_balance NUMERIC(14,2);
    v_limit   NUMERIC(14,2);
    v_amount  NUMERIC(14,2);
BEGIN
    IF NEW.payment_method = 'account' THEN
        v_amount := NEW.volume * NEW.sell_price;  -- total_amount not yet generated at BEFORE stage

        SELECT current_balance, monthly_limit INTO v_balance, v_limit
        FROM customers WHERE id = NEW.customer_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Customer % not found', NEW.customer_id;
        END IF;

        IF v_limit > 0 AND (v_balance + v_amount) > v_limit THEN
            RAISE EXCEPTION 'Credit limit exceeded for customer %: balance % + sale % > limit %',
                NEW.customer_id, v_balance, v_amount, v_limit;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_before_sale_check_limit
BEFORE INSERT ON sales
FOR EACH ROW EXECUTE FUNCTION trg_fn_check_credit_limit();

-- ---------------------------------------------------------------------
-- Trigger: after a sale is inserted -> reduce that branch's stock, post
-- customer transaction (if on account), refresh fuel + financial reports
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_sale_after_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_new_balance NUMERIC(14,2);
    v_company_id  INT;
BEGIN
    UPDATE fuel_stock
    SET current_volume = current_volume - NEW.volume, updated_at = now()
    WHERE branch_id = NEW.branch_id AND fuel_type_id = NEW.fuel_type_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'No stock record for branch % / fuel_type %', NEW.branch_id, NEW.fuel_type_id;
    END IF;

    IF NEW.payment_method = 'account' THEN
        UPDATE customers
        SET current_balance = current_balance + NEW.total_amount
        WHERE id = NEW.customer_id
        RETURNING current_balance INTO v_new_balance;

        INSERT INTO customer_transactions(
            branch_id, customer_id, txn_type, sale_id, amount, balance_after, recorded_by, transaction_date, notes
        ) VALUES (
            NEW.branch_id, NEW.customer_id, 'fill_up', NEW.id, NEW.total_amount, v_new_balance, NEW.served_by, NEW.sale_date,
            'Fuel fill-up on account'
        );
    END IF;

    PERFORM fn_refresh_daily_report(NEW.branch_id, NEW.sale_date::date, NEW.fuel_type_id);
    PERFORM fn_refresh_financial_summary(NEW.branch_id, NEW.sale_date::date);

    SELECT company_id INTO v_company_id FROM company_branches WHERE id = NEW.branch_id;
    PERFORM fn_log_activity(
        v_company_id, NEW.branch_id, NEW.served_by, 'sale',
        format('Sold %s L (%s)', NEW.volume, NEW.payment_method),
        NEW.total_amount, 'sales', NEW.id
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_sale_insert
AFTER INSERT ON sales
FOR EACH ROW EXECUTE FUNCTION trg_fn_sale_after_insert();

-- ---------------------------------------------------------------------
-- Trigger: before a manual customer_transactions row (payment/adjustment)
-- is inserted -> apply it to the customer's balance and stamp balance_after.
-- ("fill_up" rows are created only by the sales trigger above, already
-- carrying the correct balance_after, so this trigger skips them.)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_customer_txn_before_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_balance NUMERIC(14,2);
BEGIN
    UPDATE customers
    SET current_balance = current_balance + NEW.amount
    WHERE id = NEW.customer_id
    RETURNING current_balance INTO v_balance;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Customer % not found', NEW.customer_id;
    END IF;

    NEW.balance_after := v_balance;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_before_customer_txn
BEFORE INSERT ON customer_transactions
FOR EACH ROW
WHEN (NEW.txn_type <> 'fill_up')
EXECUTE FUNCTION trg_fn_customer_txn_before_insert();

-- Refresh the financial summary whenever a customer transaction lands
-- (mainly matters for 'payment' rows — debt collected is cash income).
CREATE OR REPLACE FUNCTION trg_fn_customer_txn_after_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_company_id INT;
BEGIN
    PERFORM fn_refresh_financial_summary(NEW.branch_id, NEW.transaction_date::date);

    IF NEW.txn_type = 'payment' THEN
        SELECT company_id INTO v_company_id FROM company_branches WHERE id = NEW.branch_id;
        PERFORM fn_log_activity(
            v_company_id, NEW.branch_id, NEW.recorded_by, 'customer_payment',
            'Payment received from customer', -NEW.amount, 'customer_transactions', NEW.id
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_customer_txn_insert
AFTER INSERT ON customer_transactions
FOR EACH ROW EXECUTE FUNCTION trg_fn_customer_txn_after_insert();

-- ---------------------------------------------------------------------
-- Trigger: after a supplier payment is inserted -> update receipt's
-- amount_paid / payment_status (marks the invoice paid/partial/unpaid)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_fn_supplier_payment_after_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_branch_id  INT;
    v_company_id INT;
    v_category_id INT;
    v_supplier_name VARCHAR(150);
    v_reference VARCHAR(100);
BEGIN
    UPDATE fuel_receipts
    SET amount_paid = amount_paid + NEW.amount
    WHERE id = NEW.receipt_id AND amount_paid + NEW.amount <= total_cost;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Payment exceeds the supplier invoice balance';
    END IF;

    UPDATE fuel_receipts
    SET payment_status = CASE
        WHEN amount_paid >= total_cost THEN 'paid'::payment_status
        WHEN amount_paid > 0            THEN 'partial'::payment_status
        ELSE 'unpaid'::payment_status
    END
    WHERE id = NEW.receipt_id;

    SELECT branch_id INTO v_branch_id FROM fuel_receipts WHERE id = NEW.receipt_id;
    SELECT company_id INTO v_company_id FROM company_branches WHERE id = v_branch_id;
    SELECT id INTO v_category_id FROM expense_categories
      WHERE company_id = v_company_id AND name = 'Fuel Purchases';
    IF v_category_id IS NULL THEN
        INSERT INTO expense_categories(company_id, name)
        VALUES (v_company_id, 'Fuel Purchases') RETURNING id INTO v_category_id;
    END IF;
    SELECT s.name, fr.invoice_reference INTO v_supplier_name, v_reference
      FROM fuel_receipts fr JOIN suppliers s ON s.id = fr.supplier_id
      WHERE fr.id = NEW.receipt_id;
    INSERT INTO expenses(company_id, branch_id, name, category_id, amount,
                         reference, notes, receipt_id, supplier_payment_id,
                         expense_date, recorded_by, is_system_generated, payment_channel)
    VALUES (v_company_id, v_branch_id,
            'Fuel purchase - ' || COALESCE(v_supplier_name, 'Supplier'),
            v_category_id, NEW.amount, COALESCE(NEW.reference, v_reference),
            'Supplier invoice #' || NEW.receipt_id,
            NEW.receipt_id, NEW.id, NEW.payment_date, NEW.paid_by, TRUE,
            NEW.payment_channel);
    PERFORM fn_log_activity(
        v_company_id, v_branch_id, NEW.paid_by, 'supplier_payment',
        'Payment made against supplier invoice', NEW.amount, 'supplier_payments', NEW.id
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_supplier_payment_insert
AFTER INSERT ON supplier_payments
FOR EACH ROW EXECUTE FUNCTION trg_fn_supplier_payment_after_insert();

-- Refresh the financial summary whenever an expense is logged (covers both
-- manually entered expenses and the auto-generated one above).
CREATE OR REPLACE FUNCTION trg_fn_expense_after_insert()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.branch_id IS NOT NULL THEN
        PERFORM fn_refresh_financial_summary(NEW.branch_id, NEW.expense_date);
    END IF;

    PERFORM fn_log_activity(
        NEW.company_id, NEW.branch_id, NEW.recorded_by, 'expense',
        NEW.name, NEW.amount, 'expenses', NEW.id
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_after_expense_insert
AFTER INSERT ON expenses
FOR EACH ROW EXECUTE FUNCTION trg_fn_expense_after_insert();

-- =====================================================================
-- DIP-STICK -> VOLUME
-- Converts a dip-stick reading (cm from the tank bottom) into litres for
-- one branch + fuel type:
--   1. if a dip chart exists (tank_dip_calibrations) it is interpolated
--      linearly between the two surrounding points (clamped at the ends),
--   2. otherwise the tank is treated as even: reading / tank_height_cm
--      * capacity (fuel_stock.tank_height_cm defaults to 100 cm),
--   3. returns NULL when the tank has no capacity/calibration at all —
--      the caller must set the tank size first.
-- =====================================================================
CREATE OR REPLACE FUNCTION fn_dip_to_volume(
    p_branch_id    INT,
    p_fuel_type_id INT,
    p_dip_cm       NUMERIC
) RETURNS NUMERIC AS $$
DECLARE
    v_capacity  NUMERIC;
    v_height    NUMERIC;
    v_points    INT;
    v_lower_dip NUMERIC;
    v_lower_vol NUMERIC;
    v_upper_dip NUMERIC;
    v_upper_vol NUMERIC;
    v_volume    NUMERIC;
BEGIN
    IF p_dip_cm IS NULL OR p_dip_cm < 0 THEN
        RETURN NULL;
    END IF;

    SELECT COUNT(*) INTO v_points
      FROM tank_dip_calibrations
     WHERE branch_id = p_branch_id AND fuel_type_id = p_fuel_type_id;

    IF v_points > 0 THEN
        SELECT dip_cm, volume_litres INTO v_lower_dip, v_lower_vol
          FROM tank_dip_calibrations
         WHERE branch_id = p_branch_id AND fuel_type_id = p_fuel_type_id
           AND dip_cm <= p_dip_cm
         ORDER BY dip_cm DESC LIMIT 1;

        SELECT dip_cm, volume_litres INTO v_upper_dip, v_upper_vol
          FROM tank_dip_calibrations
         WHERE branch_id = p_branch_id AND fuel_type_id = p_fuel_type_id
           AND dip_cm >= p_dip_cm
         ORDER BY dip_cm ASC LIMIT 1;

        IF v_lower_dip IS NOT NULL AND v_upper_dip IS NOT NULL THEN
            IF v_upper_dip = v_lower_dip THEN
                RETURN ROUND(v_lower_vol, 3);
            END IF;
            RETURN ROUND(
                v_lower_vol
                + (p_dip_cm - v_lower_dip) * (v_upper_vol - v_lower_vol)
                  / (v_upper_dip - v_lower_dip),
                3
            );
        ELSIF v_upper_dip IS NOT NULL THEN
            -- below the lowest chart point: scale up from an empty tank
            IF v_upper_dip = 0 THEN
                RETURN ROUND(v_upper_vol, 3);
            END IF;
            RETURN ROUND(p_dip_cm * v_upper_vol / v_upper_dip, 3);
        END IF;
    END IF;

    -- No chart point at/below this reading: fall back to the tank's own
    -- calibrated height and capacity (the straight-line "seeded" curve).
    SELECT fs.capacity, fs.tank_height_cm INTO v_capacity, v_height
      FROM fuel_stock fs
     WHERE fs.branch_id = p_branch_id AND fs.fuel_type_id = p_fuel_type_id;

    IF v_capacity IS NULL OR v_capacity <= 0 THEN
        RETURN NULL;
    END IF;
    IF v_height IS NULL OR v_height <= 0 THEN
        v_height := 100;
    END IF;

    v_volume := ROUND(LEAST(p_dip_cm, v_height) * v_capacity / v_height, 3);

    IF v_points > 0 AND v_lower_dip IS NOT NULL AND v_lower_dip > 0 THEN
        -- above the highest chart point: carry on proportionally, capped by capacity
        v_volume := ROUND(p_dip_cm * v_lower_vol / v_lower_dip, 3);
    END IF;

    RETURN LEAST(v_volume, v_capacity);
END;
$$ LANGUAGE plpgsql STABLE;

-- =====================================================================
-- REPORTING VIEWS / FUNCTIONS (date-range filterable, by branch and/or
-- whole company)
-- =====================================================================

-- Fuel volume + monetary report, per branch per fuel type per day
CREATE OR REPLACE FUNCTION get_fuel_report_range(
    p_start DATE, p_end DATE,
    p_company_id INT DEFAULT NULL, p_branch_id INT DEFAULT NULL, p_fuel_type_id INT DEFAULT NULL
) RETURNS TABLE (
    branch_id INT, company_id INT, branch_name VARCHAR, report_date DATE,
    fuel_type_id INT, fuel_type_name VARCHAR,
    opening_stock NUMERIC, received_volume NUMERIC, sold_volume NUMERIC, credit_volume NUMERIC,
    closing_stock NUMERIC, total_purchase_amount NUMERIC, total_sales_amount NUMERIC, credit_amount NUMERIC,
    generated_at TIMESTAMPTZ
) AS $$
    SELECT dfr.branch_id, cb.company_id, cb.branch_name, dfr.report_date,
           dfr.fuel_type_id, ft.name,
           dfr.opening_stock, dfr.received_volume, dfr.sold_volume, dfr.credit_volume,
           dfr.closing_stock, dfr.total_purchase_amount, dfr.total_sales_amount, dfr.credit_amount,
           dfr.generated_at
    FROM daily_fuel_reports dfr
    JOIN company_branches cb ON cb.id = dfr.branch_id
    JOIN fuel_types ft ON ft.id = dfr.fuel_type_id
    WHERE dfr.report_date BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR dfr.branch_id = p_branch_id)
      AND (p_company_id IS NULL OR cb.company_id = p_company_id)
      AND (p_fuel_type_id IS NULL OR dfr.fuel_type_id = p_fuel_type_id)
    ORDER BY dfr.report_date, dfr.branch_id, dfr.fuel_type_id;
$$ LANGUAGE sql STABLE;

-- Kiosk products, mixed POS orders and company module permissions.
CREATE TABLE product_categories (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, name)
);
CREATE TABLE kiosk_products (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    branch_id INT NOT NULL REFERENCES company_branches(id),
    category_id INT NOT NULL REFERENCES product_categories(id),
    name VARCHAR(150) NOT NULL,
    cost_price NUMERIC(12,2) NOT NULL CHECK(cost_price >= 0),
    sell_price NUMERIC(12,2) NOT NULL CHECK(sell_price >= 0),
    stock_quantity NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK(stock_quantity >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(branch_id, name)
);
CREATE TABLE kiosk_sales (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    branch_id INT NOT NULL REFERENCES company_branches(id),
    shift_id INT REFERENCES shifts(id),
    payment_method payment_method NOT NULL,
    total_amount NUMERIC(14,2) NOT NULL,
    cash_received NUMERIC(14,2),
    change_given NUMERIC(14,2),
    served_by INT NOT NULL REFERENCES users(id),
    sale_date TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE kiosk_sale_items (
    id SERIAL PRIMARY KEY,
    sale_id INT NOT NULL REFERENCES kiosk_sales(id) ON DELETE CASCADE,
    product_id INT REFERENCES kiosk_products(id),
    fuel_sale_id INT REFERENCES sales(id),
    item_name VARCHAR(150) NOT NULL,
    item_type VARCHAR(10) NOT NULL CHECK(item_type IN ('product','fuel')),
    quantity NUMERIC(14,3) NOT NULL CHECK(quantity > 0),
    unit_cost NUMERIC(12,2) NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    total_amount NUMERIC(14,2) NOT NULL
);
CREATE TABLE company_sales_settings (
    company_id INT PRIMARY KEY REFERENCES companies(id),
    kiosk_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    kiosk_sell_roles JSONB NOT NULL DEFAULT '["manager","supervisor","developer"]'::jsonb,
    kiosk_view_roles JSONB NOT NULL DEFAULT '["manager","supervisor","developer"]'::jsonb,
    updated_by INT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Financial report (opening/income/expenses/closing), per branch per day
CREATE OR REPLACE FUNCTION get_financial_report_range(
    p_start DATE, p_end DATE, p_company_id INT DEFAULT NULL, p_branch_id INT DEFAULT NULL
) RETURNS TABLE (
    branch_id INT, company_id INT, branch_name VARCHAR, summary_date DATE,
    opening_balance NUMERIC, cash_sales_income NUMERIC,
    customer_payments_income NUMERIC, total_income NUMERIC, total_expenses NUMERIC, closing_balance NUMERIC,
    generated_at TIMESTAMPTZ
) AS $$
    SELECT dfs.branch_id, cb.company_id, cb.branch_name, dfs.summary_date,
           dfs.opening_balance, dfs.cash_sales_income,
           dfs.customer_payments_income, dfs.total_income, dfs.total_expenses, dfs.closing_balance,
           dfs.generated_at
    FROM daily_financial_summary dfs
    JOIN company_branches cb ON cb.id = dfs.branch_id
    WHERE dfs.summary_date BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR dfs.branch_id = p_branch_id)
      AND (p_company_id IS NULL OR cb.company_id = p_company_id)
    ORDER BY dfs.summary_date, dfs.branch_id;
$$ LANGUAGE sql STABLE;

-- Raw sales, filterable by date range / company / branch / fuel type / customer
CREATE OR REPLACE FUNCTION get_sales_range(
    p_start TIMESTAMPTZ, p_end TIMESTAMPTZ,
    p_company_id INT DEFAULT NULL, p_branch_id INT DEFAULT NULL,
    p_fuel_type_id INT DEFAULT NULL, p_customer_id INT DEFAULT NULL
)
RETURNS SETOF sales AS $$
    SELECT s.* FROM sales s
    JOIN company_branches cb ON cb.id = s.branch_id
    WHERE s.sale_date BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR s.branch_id = p_branch_id)
      AND (p_company_id IS NULL OR cb.company_id = p_company_id)
      AND (p_fuel_type_id IS NULL OR s.fuel_type_id = p_fuel_type_id)
      AND (p_customer_id IS NULL OR s.customer_id = p_customer_id)
    ORDER BY s.sale_date;
$$ LANGUAGE sql STABLE;

-- One customer's statement (fill-ups + payments + adjustments) for a date range
CREATE OR REPLACE FUNCTION get_customer_statement(p_customer_id INT, p_start DATE, p_end DATE)
RETURNS SETOF customer_transactions AS $$
    SELECT * FROM customer_transactions
    WHERE customer_id = p_customer_id
      AND transaction_date::date BETWEEN p_start AND p_end
    ORDER BY transaction_date;
$$ LANGUAGE sql STABLE;

-- Expenses with their category name attached
CREATE OR REPLACE VIEW v_expenses AS
SELECT e.id, e.company_id, e.branch_id, e.name, e.category_id, ec.name AS category_name, e.amount,
       e.reference, e.notes, e.receipt_id, e.expense_date, e.recorded_by,
       e.is_system_generated, e.created_at
FROM expenses e
JOIN expense_categories ec ON ec.id = e.category_id;

-- Expenses for a date range, optionally by company/branch/category.
-- branch_id = NULL rows are company-wide expenses — they still match a
-- p_company_id filter, but only match p_branch_id when explicitly asked
-- for unallocated (company-wide) expenses via a NULL check on the client side.
CREATE OR REPLACE FUNCTION get_expense_report_range(
    p_start DATE, p_end DATE,
    p_company_id INT DEFAULT NULL, p_branch_id INT DEFAULT NULL, p_category_id INT DEFAULT NULL
)
RETURNS SETOF v_expenses AS $$
    SELECT e.* FROM v_expenses e
    WHERE e.expense_date BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR e.branch_id = p_branch_id)
      AND (p_company_id IS NULL OR e.company_id = p_company_id)
      AND (p_category_id IS NULL OR e.category_id = p_category_id)
    ORDER BY e.expense_date;
$$ LANGUAGE sql STABLE;

-- Fuel given to customers on credit ("pending" — not yet cash income),
-- one row per account sale
CREATE OR REPLACE VIEW v_credit_fuel_given AS
SELECT s.id AS sale_id, s.branch_id, s.sale_date, s.fuel_type_id, ft.name AS fuel_type_name,
       s.customer_id, c.name AS customer_name, c.company_name,
       s.volume, s.total_amount, s.served_by
FROM sales s
JOIN customers c  ON c.id = s.customer_id
JOIN fuel_types ft ON ft.id = s.fuel_type_id
WHERE s.payment_method = 'account';

-- Credit fuel given, for a date range, optionally by company/branch/customer
CREATE OR REPLACE FUNCTION get_credit_report_range(
    p_start TIMESTAMPTZ, p_end TIMESTAMPTZ,
    p_company_id INT DEFAULT NULL, p_branch_id INT DEFAULT NULL, p_customer_id INT DEFAULT NULL
)
RETURNS SETOF v_credit_fuel_given AS $$
    SELECT v.* FROM v_credit_fuel_given v
    JOIN company_branches cb ON cb.id = v.branch_id
    WHERE v.sale_date BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR v.branch_id = p_branch_id)
      AND (p_company_id IS NULL OR cb.company_id = p_company_id)
      AND (p_customer_id IS NULL OR v.customer_id = p_customer_id)
    ORDER BY v.sale_date;
$$ LANGUAGE sql STABLE;

-- Quick summary: sales grouped by branch + day + fuel type (handy for charts)
CREATE OR REPLACE VIEW v_sales_summary AS
SELECT branch_id,
       sale_date::date AS sale_date,
       fuel_type_id,
       SUM(volume)        AS total_volume,
       SUM(total_amount)  AS total_amount,
       COUNT(*)           AS num_transactions
FROM sales
GROUP BY branch_id, sale_date::date, fuel_type_id;

-- Quick summary: receipts grouped by branch + day + supplier + fuel type
CREATE OR REPLACE VIEW v_receipts_summary AS
SELECT branch_id, received_date, supplier_id, fuel_type_id,
       SUM(volume)     AS total_volume,
       SUM(total_cost) AS total_cost
FROM fuel_receipts
GROUP BY branch_id, received_date, supplier_id, fuel_type_id;

-- Outstanding supplier invoices at a glance
CREATE OR REPLACE VIEW v_outstanding_supplier_invoices AS
SELECT r.id, r.branch_id, s.company_id, r.supplier_id, s.name AS supplier_name, r.invoice_reference,
       r.total_cost, r.amount_paid, (r.total_cost - r.amount_paid) AS balance_due,
       r.payment_status, r.received_date
FROM fuel_receipts r
JOIN suppliers s ON s.id = r.supplier_id
WHERE r.payment_status <> 'paid';

-- Customers currently owing money
CREATE OR REPLACE VIEW v_customers_owing AS
SELECT id, company_id, company_name, name, phone, monthly_limit, current_balance
FROM customers
WHERE current_balance > 0;

-- Company-wide daily rollup: every branch's financial summary added
-- together, plus any company-wide (head-office, branch_id IS NULL)
-- expenses folded into total_expenses. There's no single combined
-- opening/closing balance here since each branch keeps its own till —
-- use get_financial_report_range() per branch for that.
CREATE OR REPLACE VIEW v_company_daily_totals AS
WITH branch_totals AS (
    SELECT cb.company_id, dfs.summary_date,
           SUM(dfs.cash_sales_income)        AS branch_cash_sales_income,
           SUM(dfs.customer_payments_income) AS branch_customer_payments_income,
           SUM(dfs.total_income)             AS total_income,
           SUM(dfs.total_expenses)           AS branch_total_expenses
    FROM daily_financial_summary dfs
    JOIN company_branches cb ON cb.id = dfs.branch_id
    GROUP BY cb.company_id, dfs.summary_date
),
company_wide_expenses AS (
    SELECT company_id, expense_date AS summary_date, SUM(amount) AS unallocated_expenses
    FROM expenses
    WHERE branch_id IS NULL
    GROUP BY company_id, expense_date
)
SELECT COALESCE(bt.company_id, cwe.company_id) AS company_id,
       COALESCE(bt.summary_date, cwe.summary_date) AS summary_date,
       COALESCE(bt.branch_cash_sales_income, 0)        AS branch_cash_sales_income,
       COALESCE(bt.branch_customer_payments_income, 0) AS branch_customer_payments_income,
       COALESCE(bt.total_income, 0)                    AS total_income,
       COALESCE(bt.branch_total_expenses, 0) + COALESCE(cwe.unallocated_expenses, 0) AS total_expenses,
       COALESCE(cwe.unallocated_expenses, 0)           AS unallocated_company_expenses
FROM branch_totals bt
FULL OUTER JOIN company_wide_expenses cwe
  ON bt.company_id = cwe.company_id AND bt.summary_date = cwe.summary_date;

-- Every branch's current price for every fuel type, with names attached —
-- what HQ sees when comparing pricing across branches.
CREATE OR REPLACE VIEW v_branch_fuel_prices AS
SELECT bfp.branch_id, cb.company_id, cb.branch_name,
       bfp.fuel_type_id, ft.name AS fuel_type_name,
       bfp.current_cost, bfp.sell_price,
       ft.default_cost, ft.default_sell_price,
       bfp.updated_at, bfp.updated_by
FROM branch_fuel_prices bfp
JOIN company_branches cb ON cb.id = bfp.branch_id
JOIN fuel_types ft ON ft.id = bfp.fuel_type_id;

-- Stock level + tank capacity per branch per fuel type, with % full —
-- what HQ sees when checking how full each branch's tanks are.
CREATE OR REPLACE VIEW v_branch_stock_status AS
SELECT fs.branch_id, cb.company_id, cb.branch_name,
       fs.fuel_type_id, ft.name AS fuel_type_name,
       fs.current_volume, fs.capacity, fs.tank_height_cm,
       CASE WHEN fs.capacity IS NOT NULL AND fs.capacity > 0
            THEN ROUND((fs.current_volume / fs.capacity) * 100, 1)
            ELSE NULL END AS percent_full,
       fs.updated_at
FROM fuel_stock fs
JOIN company_branches cb ON cb.id = fs.branch_id
JOIN fuel_types ft ON ft.id = fs.fuel_type_id;

-- Same, rolled up to the whole company per fuel type — total volume on
-- hand and total tank capacity across every branch, with a combined % full.
CREATE OR REPLACE VIEW v_company_stock_totals AS
SELECT cb.company_id, fs.fuel_type_id, ft.name AS fuel_type_name,
       SUM(fs.current_volume) AS total_current_volume,
       SUM(fs.capacity)       AS total_capacity,
       CASE WHEN SUM(fs.capacity) > 0
            THEN ROUND((SUM(fs.current_volume) / SUM(fs.capacity)) * 100, 1)
            ELSE NULL END AS percent_full
FROM fuel_stock fs
JOIN company_branches cb ON cb.id = fs.branch_id
JOIN fuel_types ft ON ft.id = fs.fuel_type_id
GROUP BY cb.company_id, fs.fuel_type_id, ft.name;

-- HQ audit trail: every sale, receipt, payment, expense and price change,
-- filterable by date range / branch / activity type — always scoped to one
-- company so one tenant can never see another's trail.
CREATE OR REPLACE FUNCTION get_activity_log_range(
    p_start TIMESTAMPTZ, p_end TIMESTAMPTZ,
    p_company_id INT, p_branch_id INT DEFAULT NULL, p_activity_type VARCHAR DEFAULT NULL
)
RETURNS SETOF activity_log AS $$
    SELECT * FROM activity_log
    WHERE company_id = p_company_id
      AND created_at BETWEEN p_start AND p_end
      AND (p_branch_id IS NULL OR branch_id = p_branch_id)
      AND (p_activity_type IS NULL OR activity_type = p_activity_type)
    ORDER BY created_at DESC;
$$ LANGUAGE sql STABLE;
