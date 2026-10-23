-- ===========================================================================
-- Migration 001 — fuel receiving upgrades (idempotent; safe to re-run)
--   * suppliers.vat_number          (optional VAT / tax id on a supplier)
--   * fuel_stock.tank_height_cm     (calibrated dip-stick height in cm)
--   * fuel_receipts.dip_before_cm / dip_after_cm  (dip readings on a delivery)
--   * tank_dip_calibrations         (per-branch dip chart: cm -> litres)
--   * fn_dip_to_volume()           (dip reading -> litres)
--   * v_branch_stock_status         (now exposes tank_height_cm)
-- Apply with:  psql -d fuel_system -f migrations/001_fuel_receiving.sql
-- ===========================================================================

ALTER TABLE suppliers     ADD COLUMN IF NOT EXISTS vat_number   VARCHAR(50);
ALTER TABLE fuel_stock    ADD COLUMN IF NOT EXISTS tank_height_cm NUMERIC(8,2) NOT NULL DEFAULT 100;
ALTER TABLE fuel_receipts ADD COLUMN IF NOT EXISTS dip_before_cm NUMERIC(8,2);
ALTER TABLE fuel_receipts ADD COLUMN IF NOT EXISTS dip_after_cm  NUMERIC(8,2);

CREATE TABLE IF NOT EXISTS tank_dip_calibrations (
    id              SERIAL PRIMARY KEY,
    branch_id       INT NOT NULL REFERENCES company_branches(id) ON DELETE CASCADE,
    fuel_type_id    INT NOT NULL REFERENCES fuel_types(id) ON DELETE CASCADE,
    dip_cm          NUMERIC(8,2)  NOT NULL CHECK (dip_cm >= 0),
    volume_litres   NUMERIC(14,3) NOT NULL CHECK (volume_litres >= 0),
    created_by      INT REFERENCES users(id),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (branch_id, fuel_type_id, dip_cm)
);
CREATE INDEX IF NOT EXISTS idx_dip_cal_branch ON tank_dip_calibrations(branch_id, fuel_type_id);

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

-- The view gains a column in the middle, which CREATE OR REPLACE cannot do,
-- so drop and recreate it (no other view depends on it).
DROP VIEW IF EXISTS v_branch_stock_status;
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

-- Keep the API's role able to use everything the migration just added
-- (fresh objects above are owned by whoever ran the migration).
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'fuel_user') THEN
    GRANT ALL ON SCHEMA public TO fuel_user;
    GRANT ALL PRIVILEGES ON ALL TABLES    IN SCHEMA public TO fuel_user;
    GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO fuel_user;
    GRANT EXECUTE ON ALL FUNCTIONS        IN SCHEMA public TO fuel_user;
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO fuel_user';
    EXECUTE 'ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO fuel_user';
  END IF;
END $$;
