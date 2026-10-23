CREATE TABLE IF NOT EXISTS shift_templates (
    id SERIAL PRIMARY KEY,
    company_id INT NOT NULL REFERENCES companies(id),
    name VARCHAR(60) NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    CHECK (start_time <> end_time),
    UNIQUE (company_id, name)
);
