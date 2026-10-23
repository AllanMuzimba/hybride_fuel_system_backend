BEGIN;

CREATE TABLE IF NOT EXISTS product_categories (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    name VARCHAR(100) NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(company_id, name)
);

CREATE TABLE IF NOT EXISTS kiosk_products (
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
CREATE INDEX IF NOT EXISTS idx_kiosk_products_company_branch ON kiosk_products(company_id, branch_id);

CREATE TABLE IF NOT EXISTS kiosk_sales (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    branch_id INT NOT NULL REFERENCES company_branches(id),
    shift_id INT REFERENCES shifts(id),
    payment_method payment_method NOT NULL,
    total_amount NUMERIC(14,2) NOT NULL CHECK(total_amount >= 0),
    cash_received NUMERIC(14,2),
    change_given NUMERIC(14,2),
    served_by INT NOT NULL REFERENCES users(id),
    sale_date TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_kiosk_sales_company_date ON kiosk_sales(company_id, sale_date DESC);

CREATE TABLE IF NOT EXISTS kiosk_sale_items (
    id SERIAL PRIMARY KEY,
    sale_id INT NOT NULL REFERENCES kiosk_sales(id) ON DELETE CASCADE,
    product_id INT REFERENCES kiosk_products(id),
    fuel_sale_id INT REFERENCES sales(id),
    item_name VARCHAR(150) NOT NULL,
    item_type VARCHAR(10) NOT NULL CHECK(item_type IN ('product','fuel')),
    quantity NUMERIC(14,3) NOT NULL CHECK(quantity > 0),
    unit_cost NUMERIC(12,2) NOT NULL,
    unit_price NUMERIC(12,2) NOT NULL,
    total_amount NUMERIC(14,2) NOT NULL,
    CHECK ((product_id IS NOT NULL)::int + (fuel_sale_id IS NOT NULL)::int = 1)
);

CREATE TABLE IF NOT EXISTS company_sales_settings (
    company_id INT PRIMARY KEY REFERENCES companies(id),
    kiosk_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    kiosk_sell_roles JSONB NOT NULL DEFAULT '["manager","supervisor","developer"]'::jsonb,
    kiosk_view_roles JSONB NOT NULL DEFAULT '["manager","supervisor","developer"]'::jsonb,
    updated_by INT REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
