-- Multiple physical tanks may store the same fuel at one branch.
-- fuel_stock remains the authoritative branch+fuel aggregate used by sales
-- and daily reports; fuel_tanks provides the physical breakdown.
CREATE TABLE IF NOT EXISTS fuel_tanks (
    id              SERIAL PRIMARY KEY,
    branch_id       INT NOT NULL REFERENCES company_branches(id) ON DELETE CASCADE,
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id),
    name            VARCHAR(100) NOT NULL,
    current_volume  NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK (current_volume >= 0),
    capacity        NUMERIC(14,3) NOT NULL CHECK (capacity > 0),
    tank_height_cm  NUMERIC(8,2) NOT NULL DEFAULT 100 CHECK (tank_height_cm > 0),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (branch_id, name)
);

CREATE INDEX IF NOT EXISTS idx_fuel_tanks_branch_fuel
    ON fuel_tanks(branch_id, fuel_type_id);

-- Preserve every existing installation as Tank 1, including its live stock.
INSERT INTO fuel_tanks (
    branch_id, fuel_type_id, name, current_volume, capacity,
    tank_height_cm, updated_at
)
SELECT fs.branch_id, fs.fuel_type_id,
       ft.name || ' Tank 1', fs.current_volume, fs.capacity,
       fs.tank_height_cm, fs.updated_at
FROM fuel_stock fs
JOIN fuel_types ft ON ft.id = fs.fuel_type_id
WHERE fs.capacity IS NOT NULL AND fs.capacity > 0
  AND NOT EXISTS (
      SELECT 1 FROM fuel_tanks t
      WHERE t.branch_id = fs.branch_id AND t.fuel_type_id = fs.fuel_type_id
  );

ALTER TABLE tank_dip_calibrations
    ADD COLUMN IF NOT EXISTS tank_id INT REFERENCES fuel_tanks(id) ON DELETE CASCADE;

UPDATE tank_dip_calibrations c
SET tank_id = t.id
FROM fuel_tanks t
WHERE c.tank_id IS NULL
  AND t.branch_id = c.branch_id
  AND t.fuel_type_id = c.fuel_type_id
  AND t.id = (
      SELECT MIN(t2.id) FROM fuel_tanks t2
      WHERE t2.branch_id = c.branch_id AND t2.fuel_type_id = c.fuel_type_id
  );

DROP INDEX IF EXISTS uq_tank_dip_calibrations_branch_fuel_dip;
ALTER TABLE tank_dip_calibrations
    DROP CONSTRAINT IF EXISTS tank_dip_calibrations_branch_id_fuel_type_id_dip_cm_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_tank_dip_calibrations_tank_dip
    ON tank_dip_calibrations(tank_id, dip_cm) WHERE tank_id IS NOT NULL;

ALTER TABLE fuel_receipts
    ADD COLUMN IF NOT EXISTS tank_id INT REFERENCES fuel_tanks(id);

CREATE OR REPLACE VIEW v_branch_stock_status AS
SELECT t.id AS tank_id, t.name AS tank_name,
       t.branch_id, cb.company_id, cb.branch_name,
       t.fuel_type_id, ft.name AS fuel_type_name,
       t.current_volume, t.capacity, t.tank_height_cm,
       ROUND((t.current_volume / t.capacity) * 100, 1) AS percent_full,
       t.updated_at
FROM fuel_tanks t
JOIN company_branches cb ON cb.id = t.branch_id
JOIN fuel_types ft ON ft.id = t.fuel_type_id;

CREATE OR REPLACE VIEW v_company_stock_totals AS
SELECT cb.company_id, t.fuel_type_id, ft.name AS fuel_type_name,
       SUM(t.current_volume) AS total_current_volume,
       SUM(t.capacity) AS total_capacity,
       CASE WHEN SUM(t.capacity) > 0
            THEN ROUND((SUM(t.current_volume) / SUM(t.capacity)) * 100, 1)
            ELSE NULL END AS percent_full
FROM fuel_tanks t
JOIN company_branches cb ON cb.id = t.branch_id
JOIN fuel_types ft ON ft.id = t.fuel_type_id
GROUP BY cb.company_id, t.fuel_type_id, ft.name;

CREATE OR REPLACE FUNCTION fn_tank_dip_to_volume(
    p_tank_id INT, p_dip_cm NUMERIC
) RETURNS NUMERIC AS $$
DECLARE
    v_capacity NUMERIC; v_height NUMERIC;
    v_lower_dip NUMERIC; v_lower_vol NUMERIC;
    v_upper_dip NUMERIC; v_upper_vol NUMERIC;
BEGIN
    IF p_dip_cm IS NULL OR p_dip_cm < 0 THEN RETURN NULL; END IF;
    SELECT capacity, tank_height_cm INTO v_capacity, v_height
    FROM fuel_tanks WHERE id = p_tank_id;
    IF NOT FOUND THEN RETURN NULL; END IF;

    SELECT dip_cm, volume_litres INTO v_lower_dip, v_lower_vol
    FROM tank_dip_calibrations
    WHERE tank_id = p_tank_id AND dip_cm <= p_dip_cm
    ORDER BY dip_cm DESC LIMIT 1;
    SELECT dip_cm, volume_litres INTO v_upper_dip, v_upper_vol
    FROM tank_dip_calibrations
    WHERE tank_id = p_tank_id AND dip_cm >= p_dip_cm
    ORDER BY dip_cm LIMIT 1;

    IF v_lower_dip IS NOT NULL AND v_upper_dip IS NOT NULL THEN
        IF v_upper_dip = v_lower_dip THEN RETURN ROUND(v_lower_vol, 3); END IF;
        RETURN LEAST(ROUND(v_lower_vol + (p_dip_cm - v_lower_dip) *
            (v_upper_vol - v_lower_vol) / (v_upper_dip - v_lower_dip), 3), v_capacity);
    ELSIF v_upper_dip IS NOT NULL THEN
        IF v_upper_dip = 0 THEN RETURN ROUND(v_upper_vol, 3); END IF;
        RETURN LEAST(ROUND(p_dip_cm * v_upper_vol / v_upper_dip, 3), v_capacity);
    ELSIF v_lower_dip IS NOT NULL AND v_lower_dip > 0 THEN
        RETURN LEAST(ROUND(p_dip_cm * v_lower_vol / v_lower_dip, 3), v_capacity);
    END IF;
    RETURN LEAST(ROUND(LEAST(p_dip_cm, v_height) * v_capacity / v_height, 3), v_capacity);
END;
$$ LANGUAGE plpgsql STABLE;

-- Add receipts to their selected physical tank as well as the aggregate.
CREATE OR REPLACE FUNCTION trg_fn_receipt_after_insert()
RETURNS TRIGGER AS $$
DECLARE v_company_id INT;
BEGIN
    INSERT INTO fuel_stock(branch_id, fuel_type_id, current_volume, updated_at)
    VALUES (NEW.branch_id, NEW.fuel_type_id, NEW.volume, now())
    ON CONFLICT (branch_id, fuel_type_id) DO UPDATE
      SET current_volume = fuel_stock.current_volume + NEW.volume, updated_at = now();

    IF NEW.tank_id IS NOT NULL THEN
        UPDATE fuel_tanks SET current_volume = current_volume + NEW.volume, updated_at = now()
        WHERE id = NEW.tank_id AND branch_id = NEW.branch_id AND fuel_type_id = NEW.fuel_type_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Selected tank does not match receipt branch and fuel type'; END IF;
    END IF;
    PERFORM fn_refresh_daily_report(NEW.branch_id, NEW.received_date, NEW.fuel_type_id);
    SELECT company_id INTO v_company_id FROM company_branches WHERE id = NEW.branch_id;
    PERFORM fn_log_activity(v_company_id, NEW.branch_id, NEW.received_by, 'receipt',
      format('Received %s L, invoice %s', NEW.volume, NEW.invoice_reference),
      NEW.total_cost, 'fuel_receipts', NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Keep the physical breakdown in sync with sales. Oldest non-empty tank is
-- drained first; locking makes concurrent sales safe and totals exact.
CREATE OR REPLACE FUNCTION trg_fn_allocate_sale_to_tanks()
RETURNS TRIGGER AS $$
DECLARE r RECORD; v_remaining NUMERIC := NEW.volume; v_take NUMERIC;
BEGIN
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

DROP TRIGGER IF EXISTS trg_allocate_sale_to_tanks ON sales;
CREATE TRIGGER trg_allocate_sale_to_tanks
AFTER INSERT ON sales FOR EACH ROW EXECUTE FUNCTION trg_fn_allocate_sale_to_tanks();
