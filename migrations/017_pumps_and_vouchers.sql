-- Pumps belong to one physical tank. Shift assignments restrict which
-- attendants can dispense from them and preserve opening/closing meters.
CREATE TABLE IF NOT EXISTS fuel_pumps (
    id SERIAL PRIMARY KEY,
    branch_id INT NOT NULL REFERENCES company_branches(id) ON DELETE CASCADE,
    tank_id INT NOT NULL REFERENCES fuel_tanks(id),
    name VARCHAR(100) NOT NULL,
    meter_reading NUMERIC(16,3) NOT NULL DEFAULT 0 CHECK (meter_reading >= 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (branch_id, name)
);

CREATE TABLE IF NOT EXISTS shift_pump_assignments (
    id SERIAL PRIMARY KEY,
    shift_id INT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    pump_id INT NOT NULL REFERENCES fuel_pumps(id),
    user_id INT NOT NULL REFERENCES users(id),
    opening_meter NUMERIC(16,3) NOT NULL,
    closing_meter NUMERIC(16,3),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (shift_id, pump_id)
);

ALTER TABLE sales ADD COLUMN IF NOT EXISTS pump_id INT REFERENCES fuel_pumps(id);

CREATE TABLE IF NOT EXISTS fuel_vouchers (
    id SERIAL PRIMARY KEY,
    token VARCHAR(20) NOT NULL UNIQUE,
    branch_id INT NOT NULL REFERENCES company_branches(id),
    shift_id INT NOT NULL REFERENCES shifts(id),
    fuel_type_id INT NOT NULL REFERENCES fuel_types(id),
    amount NUMERIC(14,2) NOT NULL CHECK (amount > 0),
    volume NUMERIC(14,3) NOT NULL CHECK (volume > 0),
    payment_method payment_method NOT NULL,
    customer_id INT REFERENCES customers(id),
    status VARCHAR(20) NOT NULL DEFAULT 'issued'
        CHECK (status IN ('issued', 'redeemed', 'cancelled')),
    issued_by INT NOT NULL REFERENCES users(id),
    redeemed_by INT REFERENCES users(id),
    pump_id INT REFERENCES fuel_pumps(id),
    sale_id INT REFERENCES sales(id),
    issued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    redeemed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_fuel_vouchers_token_status
    ON fuel_vouchers(token, status);

-- Sales without a pump (kiosk, legacy entries) still drain physical tanks
-- oldest-first; pump sales are handled exactly by trg_sale_pump_stock below.
CREATE OR REPLACE FUNCTION trg_fn_allocate_sale_to_tanks()
RETURNS TRIGGER AS $$
DECLARE r RECORD; v_remaining NUMERIC := NEW.volume; v_take NUMERIC;
BEGIN
    IF NEW.pump_id IS NOT NULL THEN RETURN NEW; END IF;
    FOR r IN
      SELECT id, current_volume FROM fuel_tanks
      WHERE branch_id = NEW.branch_id AND fuel_type_id = NEW.fuel_type_id
        AND current_volume > 0
      ORDER BY id FOR UPDATE
    LOOP
      EXIT WHEN v_remaining <= 0;
      v_take := LEAST(r.current_volume, v_remaining);
      UPDATE fuel_tanks SET current_volume = current_volume - v_take, updated_at = now()
      WHERE id = r.id;
      v_remaining := v_remaining - v_take;
    END LOOP;
    IF v_remaining > 0 AND EXISTS (
      SELECT 1 FROM fuel_tanks WHERE branch_id = NEW.branch_id AND fuel_type_id = NEW.fuel_type_id
    ) THEN
      RAISE EXCEPTION 'Physical tank volumes are short by % litres; reconcile tank dips before selling', v_remaining;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- A pump-linked sale updates exactly its linked physical tank and meter.
CREATE OR REPLACE FUNCTION trg_fn_sale_pump_stock()
RETURNS TRIGGER AS $$
DECLARE v_tank_id INT; v_branch_id INT; v_fuel_type_id INT;
BEGIN
    IF NEW.pump_id IS NULL THEN RETURN NEW; END IF;
    SELECT p.tank_id, p.branch_id, t.fuel_type_id
      INTO v_tank_id, v_branch_id, v_fuel_type_id
    FROM fuel_pumps p JOIN fuel_tanks t ON t.id = p.tank_id
    WHERE p.id = NEW.pump_id AND p.is_active FOR UPDATE OF p, t;
    IF NOT FOUND OR v_branch_id <> NEW.branch_id OR v_fuel_type_id <> NEW.fuel_type_id THEN
      RAISE EXCEPTION 'Pump does not match the sale branch and fuel type';
    END IF;
    UPDATE fuel_tanks SET current_volume = current_volume - NEW.volume, updated_at = now()
    WHERE id = v_tank_id AND current_volume >= NEW.volume;
    IF NOT FOUND THEN RAISE EXCEPTION 'Insufficient stock in the pump''s assigned tank'; END IF;
    UPDATE fuel_pumps SET meter_reading = meter_reading + NEW.volume WHERE id = NEW.pump_id;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sale_pump_stock ON sales;
CREATE TRIGGER trg_sale_pump_stock
AFTER INSERT ON sales FOR EACH ROW EXECUTE FUNCTION trg_fn_sale_pump_stock();
