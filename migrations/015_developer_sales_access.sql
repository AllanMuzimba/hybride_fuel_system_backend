BEGIN;

ALTER TABLE company_sales_settings
    ALTER COLUMN kiosk_sell_roles
        SET DEFAULT '["manager","supervisor","developer"]'::jsonb,
    ALTER COLUMN kiosk_view_roles
        SET DEFAULT '["manager","supervisor","developer"]'::jsonb;

UPDATE company_sales_settings
SET kiosk_sell_roles = CASE
        WHEN kiosk_sell_roles ? 'developer' THEN kiosk_sell_roles
        ELSE kiosk_sell_roles || '["developer"]'::jsonb
    END,
    kiosk_view_roles = CASE
        WHEN kiosk_view_roles ? 'developer' THEN kiosk_view_roles
        ELSE kiosk_view_roles || '["developer"]'::jsonb
    END,
    updated_at = now();

COMMIT;
