-- Each supplier payment is a cash expense on its payment date. Keep historical
-- full-invoice expenses, and backfill only invoices with no expense yet.
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS supplier_payment_id INT REFERENCES supplier_payments(id);
DROP INDEX IF EXISTS uq_expenses_receipt;
CREATE INDEX IF NOT EXISTS idx_expenses_receipt ON expenses(receipt_id) WHERE receipt_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_expenses_supplier_payment ON expenses(supplier_payment_id)
  WHERE supplier_payment_id IS NOT NULL;
DROP TRIGGER IF EXISTS trg_after_receipt_update_expense ON fuel_receipts;
DROP FUNCTION IF EXISTS trg_fn_receipt_paid_expense();

CREATE OR REPLACE FUNCTION trg_fn_supplier_payment_after_insert()
RETURNS TRIGGER AS $$
DECLARE
    v_branch_id INT;
    v_company_id INT;
    v_category_id INT;
    v_supplier_name VARCHAR(150);
    v_reference VARCHAR(100);
BEGIN
    UPDATE fuel_receipts SET amount_paid = amount_paid + NEW.amount
      WHERE id = NEW.receipt_id AND amount_paid + NEW.amount <= total_cost;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Payment exceeds the supplier invoice balance';
    END IF;
    UPDATE fuel_receipts SET payment_status = CASE
        WHEN amount_paid >= total_cost THEN 'paid'::payment_status
        WHEN amount_paid > 0 THEN 'partial'::payment_status
        ELSE 'unpaid'::payment_status END
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
                         expense_date, recorded_by, is_system_generated)
    VALUES (v_company_id, v_branch_id,
            'Fuel purchase - ' || COALESCE(v_supplier_name, 'Supplier'),
            v_category_id, NEW.amount, COALESCE(NEW.reference, v_reference),
            'Supplier invoice #' || NEW.receipt_id,
            NEW.receipt_id, NEW.id, NEW.payment_date, NEW.paid_by, TRUE);
    PERFORM fn_log_activity(v_company_id, v_branch_id, NEW.paid_by,
        'supplier_payment', 'Payment made against supplier invoice',
        NEW.amount, 'supplier_payments', NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

INSERT INTO expenses(company_id, branch_id, name, category_id, amount,
                     reference, notes, receipt_id, supplier_payment_id,
                     expense_date, recorded_by, is_system_generated)
SELECT cb.company_id, fr.branch_id, 'Fuel purchase - ' || s.name,
       ec.id, sp.amount, COALESCE(sp.reference, fr.invoice_reference),
       'Supplier invoice #' || fr.id, fr.id, sp.id, sp.payment_date,
       sp.paid_by, TRUE
FROM supplier_payments sp
JOIN fuel_receipts fr ON fr.id = sp.receipt_id
JOIN company_branches cb ON cb.id = fr.branch_id
JOIN suppliers s ON s.id = fr.supplier_id
JOIN expense_categories ec ON ec.company_id = cb.company_id AND ec.name = 'Fuel Purchases'
WHERE NOT EXISTS (SELECT 1 FROM expenses e WHERE e.receipt_id = fr.id)
ON CONFLICT DO NOTHING;
