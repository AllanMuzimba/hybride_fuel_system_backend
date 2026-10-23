-- Physical tank readings and automatic stock references at shift boundaries.
CREATE TABLE IF NOT EXISTS shift_fuel_readings (
    id SERIAL PRIMARY KEY,
    shift_id INT NOT NULL REFERENCES shifts(id) ON DELETE CASCADE,
    fuel_type_id INT NOT NULL REFERENCES fuel_types(id),
    opening_system_volume NUMERIC(14,3) NOT NULL DEFAULT 0,
    opening_manual_volume NUMERIC(14,3),
    opening_dip_cm NUMERIC(8,2),
    closing_system_volume NUMERIC(14,3),
    closing_manual_volume NUMERIC(14,3),
    closing_dip_cm NUMERIC(8,2),
    UNIQUE (shift_id, fuel_type_id),
    CHECK (opening_system_volume >= 0),
    CHECK (opening_manual_volume IS NULL OR opening_manual_volume >= 0),
    CHECK (opening_dip_cm IS NULL OR opening_dip_cm >= 0),
    CHECK (closing_system_volume IS NULL OR closing_system_volume >= 0),
    CHECK (closing_manual_volume IS NULL OR closing_manual_volume >= 0),
    CHECK (closing_dip_cm IS NULL OR closing_dip_cm >= 0)
);

CREATE INDEX IF NOT EXISTS idx_shift_fuel_readings_shift
    ON shift_fuel_readings(shift_id);

-- Existing open shifts need an opening reference before the new close flow
-- can collect their physical closing readings.
INSERT INTO shift_fuel_readings (
    shift_id, fuel_type_id, opening_system_volume
)
SELECT s.id, fs.fuel_type_id, fs.current_volume
FROM shifts s
JOIN fuel_stock fs ON fs.branch_id = s.branch_id
WHERE s.status = 'open'
ON CONFLICT (shift_id, fuel_type_id) DO NOTHING;
