# Fuel Management System

Multi-company, multi-branch PostgreSQL schema + FastAPI backend for fuel
stations: supplier receipts, fuel types/pricing, stock, cash/account sales,
customer credit accounts, expenses (with invoices auto-expensed on payment),
and reporting — every transaction stamped with the user, branch and company
that did it.

## 1. Tenancy model

```
companies              -- the top-level tenant ("Acme Fuels")
  company_branches      -- each company owns 1+ branches (its physical sites)
```

- **Company-level data** (shared across a company's branches): `users`,
  `suppliers`, `fuel_types`, `customers`, `expense_categories`.
- **Branch-level data** (physical/per-site): `fuel_stock`, `fuel_receipts`,
  `sales`, `daily_fuel_reports`, `daily_financial_summary`,
  `branch_fuel_prices`.
- **Pricing is per branch.** `fuel_types` holds the company identity and
  HQ's reference price (`default_cost`/`default_sell_price`); the price a
  branch actually sells at lives in `branch_fuel_prices` and moves
  independently per branch. The moment HQ defines a fuel type, or opens a
  new branch, a trigger seeds that branch's price from the HQ default —
  from there each branch can be repriced on its own via
  `PATCH /fuel-types/branch-prices`.
- **Tank capacity is per branch too.** `fuel_stock.capacity` is each
  branch's own tank size for a fuel type (`NULL` = not tracked); HQ sets it
  via `PATCH /fuel-types/stock/capacity`. `GET /fuel-types/stock` then
  reports each branch's live volume, capacity, and % full, and `GET
  /fuel-types/stock/company-totals` rolls that up across every branch.
- `expenses` can be company-wide (`branch_id` NULL — e.g. head-office rent)
  or attributed to one branch's till.
- A `users` row has `branch_id` set (that's the *only* branch they work at —
  typically `attendant`/`supervisor`) or NULL (company-wide access —
  typically `manager`/`director`/`developer`, i.e. "HQ"). An HQ user isn't
  restricted to one branch on any read or report endpoint — pass `branch_id`
  on any of them to "switch" to that branch's view, or omit it for the
  whole company.
- **`activity_log`** is an append-only audit trail: every sale, receipt,
  supplier/customer payment, expense and price change is logged there by a
  trigger (who did it, at which branch, when, how much) — `GET
  /reports/activity-log` is HQ's audit view across every branch.
- The database itself refuses cross-company mixing: a trigger checks that a
  receipt's branch/supplier/fuel type, a sale's branch/fuel type/customer,
  and an expense's branch/category all belong to the *same* company before
  the row is allowed in.

## 2. Set up the database

```bash
createdb fuel_system
psql -d fuel_system -f schema.sql
```

`schema.sql` creates every table, enum, view, function and trigger. Key
automatic behaviour, all handled by triggers (no app-side bookkeeping needed):

- **Receiving fuel** → adds to that branch's `fuel_stock`, refreshes that
  branch's daily report.
- **Selling fuel** → snapshots cost/sell price from *that branch's own*
  `branch_fuel_prices` row (not a company-wide price), subtracts from that
  branch's `fuel_stock`; if `payment_method = 'account'`, posts a `fill_up`
  row to `customer_transactions` and raises the customer's balance — and a
  **before-insert trigger blocks the sale** if it would push the customer
  over their `monthly_limit`.
- **Supplier payments** → update the receipt's `amount_paid` and flip
  `payment_status` to `partial`/`paid`.
- **When an invoice flips to `paid`** → a matching row is auto-inserted into
  `expenses` (category `Fuel Purchases`, `is_system_generated = true`,
  attributed to that receipt's company + branch) — you never enter it twice.
- **Customer payments** → reduce their balance, stamp `balance_after`, and
  (if you tell it which branch's till collected the cash) count as that
  branch's income for the day.
- **Manual expenses** (rent, salaries, maintenance, ...) → recorded via
  `POST /expenses`, either company-wide or against one branch.
- **Daily fuel report** (`daily_fuel_reports`, one row per day per BRANCH
  per fuel type) → opening stock, received, sold (total — cash + credit,
  since the fuel physically left either way), `credit_volume`/`credit_amount`
  (the portion given to customers on account), closing stock, purchase $,
  sales $.
- **Daily financial summary** (`daily_financial_summary`, one row per day
  per BRANCH, cash-basis) → opening balance, cash/card/mobile-money sales
  income, debt *collected* from customers that day, total expenses, closing
  balance. A credit sale itself is **not** income here — only when the
  customer later pays it off does it show up as `customer_payments_income`.
- Call `SELECT fn_refresh_report_range(start, end[, branch_id]);` after
  loading historical data or fixing a backdated entry.

Date-range reports are plain functions/views, tenant-aware (pass
`company_id` and optionally `branch_id`):
- `get_fuel_report_range(start, end, company_id, branch_id, fuel_type_id)`
- `get_financial_report_range(start, end, company_id, branch_id)` and
  `v_company_daily_totals` (a company-wide rollup across all branches)
- `get_expense_report_range(start, end, company_id, branch_id, category_id)`
- `get_credit_report_range(start, end, company_id, branch_id, customer_id)` —
  fuel given on credit ("pending", not yet income)
- `get_sales_range(start, end, branch_id, fuel_type_id, customer_id)`
- `get_customer_statement(customer_id, start, end)`
- `v_sales_summary`, `v_receipts_summary`, `v_outstanding_supplier_invoices`,
  `v_customers_owing`, `v_expenses`, `v_credit_fuel_given`

`GET /reports/full-summary?start_date=...&end_date=...` on the API ties all
three (fuel volumes, financial cash flow, credit given) into one response
for your company (optionally one branch), plus today's total outstanding
customer debt — so the whole fuel movement for a day or a range reconciles
regardless of whether it was sold for cash or extended on credit.

## 3. Set up the API

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env    # edit DATABASE_URL and JWT_SECRET_KEY
python create_first_company.py     # creates your first company, branch, and 'developer' login
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Browse to `http://localhost:8000/docs` for interactive Swagger docs.

Onboarding a **new** company later doesn't need the script — it's just:
1. `POST /companies` (public — name, slug, address/lat/lng, phone, email, url)
2. `POST /companies/{company_id}/bootstrap-admin` (public, but only works
   once — while that company has zero users) to create its first
   `developer` login
3. From there, that user logs in via `POST /auth/login` and uses the normal
   (authenticated) endpoints to add branches, staff, suppliers, fuel types, etc.

## 4. Roles

`attendant` → `supervisor` → `manager` → `director` → `developer` (each tier
can do what the ones below it can, plus more — enforced per-endpoint via
`require_role(...)` in the routers). Examples:
- Any logged-in user can record a sale (`served_by` = them, whoever's on shift).
- `supervisor`+ can register suppliers/customers and receive fuel.
- `manager`+ can edit fuel prices, pay supplier invoices, and create expense categories.
- `director`+ can create/deactivate application users and branches.

A user with `branch_id` set can only touch that one branch's data
(`auth.verify_branch_access` enforces this on every branch-scoped write);
a user with `branch_id = NULL` can operate across every branch of their
company. Adjust the `require_role(...)` calls in `api/routers/*.py` to
match how you actually want to split responsibilities.

## 5. Connecting from Flutter

1. **Login**: `POST /auth/login` as `application/x-www-form-urlencoded` with
   `username` and `password` (FastAPI's standard OAuth2 password flow).
   Returns `{access_token, role, user_id, full_name, company_id, branch_id}`.
2. Store `access_token` (e.g. `flutter_secure_storage`) and send it as
   `Authorization: Bearer <token>` on every other request. Every list/report
   endpoint automatically scopes itself to the logged-in user's company —
   you never pass `company_id` yourself.
3. If the user's `branch_id` came back non-null, that's the only branch
   they can post receipts/sales against — otherwise let them pick a branch
   from `GET /branches`.
4. Everything else is plain JSON REST — e.g.:
   - `GET /fuel-types` / `GET /fuel-types/stock?branch_id=...` (volume,
     capacity, % full) / `GET /fuel-types/stock/company-totals`
   - `PATCH /fuel-types/stock/capacity` (set a branch's tank size)
   - `GET /fuel-types/branch-prices?branch_id=...` (view any branch's live
     price) / `PATCH /fuel-types/branch-prices` (reprice one branch)
   - `POST /receipts` (receive fuel), `POST /receipts/payments` (pay supplier)
   - `POST /sales` (sell fuel — cash, card, mobile money, or account)
   - `GET /customers`, `GET /customers/{id}/statement?start_date=...&end_date=...`
   - `POST /expenses`, `GET /expenses/categories`
   - `GET /reports/daily-fuel`, `GET /reports/financial`,
     `GET /reports/credit-fuel`, `GET /reports/full-summary`,
     `GET /reports/activity-log` (HQ audit trail)
5. Pull the OpenAPI schema (`/openapi.json`) into a codegen tool
   (`openapi_generator` or `chopper`) if you want typed Dart models/clients
   generated automatically instead of hand-writing them.

## 6. Project layout

```
schema.sql                    # the whole database: tenancy, tables, triggers, reports
requirements.txt
.env.example
create_first_company.py       # one-off script: first company + branch + login
api/
  database.py                  # SQLAlchemy engine/session
  models.py                    # ORM models mirroring schema.sql
  schemas.py                   # Pydantic request/response shapes
  auth.py                      # JWT + password hashing + role guard + branch-access guard
  main.py                      # FastAPI app, CORS, router wiring
  routers/
    auth_router.py
    companies_router.py        # companies, branches, tenant bootstrap
    users_router.py
    suppliers_router.py
    fuel_types_router.py
    receipts_router.py
    customers_router.py
    sales_router.py
    expenses_router.py
    reports_router.py
```
