-- Barcodes on kiosk products: typed or scanned at the product dialog and
-- at the POS, where a scanner in keyboard mode enters them followed by
-- Enter. Unique per company so two products never share a scan.
ALTER TABLE kiosk_products ADD COLUMN IF NOT EXISTS barcode VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS uq_kiosk_products_company_barcode
    ON kiosk_products(company_id, barcode) WHERE barcode IS NOT NULL;
