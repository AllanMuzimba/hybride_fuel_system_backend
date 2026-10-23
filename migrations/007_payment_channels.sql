-- Historical payment methods were not recorded. Mark them unknown so cash
-- reports never guess that an old EFT or card transaction was physical cash.
ALTER TABLE customer_transactions ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(20) NOT NULL DEFAULT 'unknown';
ALTER TABLE supplier_payments ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(20) NOT NULL DEFAULT 'unknown';
ALTER TABLE expenses ADD COLUMN IF NOT EXISTS payment_channel VARCHAR(20) NOT NULL DEFAULT 'unknown';
ALTER TABLE customer_transactions DROP CONSTRAINT IF EXISTS chk_customer_payment_channel;
ALTER TABLE customer_transactions ADD CONSTRAINT chk_customer_payment_channel
  CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown'));
ALTER TABLE supplier_payments DROP CONSTRAINT IF EXISTS chk_supplier_payment_channel;
ALTER TABLE supplier_payments ADD CONSTRAINT chk_supplier_payment_channel
  CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown'));
ALTER TABLE expenses DROP CONSTRAINT IF EXISTS chk_expense_payment_channel;
ALTER TABLE expenses ADD CONSTRAINT chk_expense_payment_channel
  CHECK (payment_channel IN ('cash', 'card', 'mobile_money', 'bank', 'unknown'));

-- Keep the existing supplier payment trigger; only its expense insertion
-- needs the recorded channel. 005 remains safe to rerun before this patch.
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
    IF NOT FOUND THEN RAISE EXCEPTION 'Payment exceeds the supplier invoice balance'; END IF;
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
                         expense_date, recorded_by, is_system_generated, payment_channel)
    VALUES (v_company_id, v_branch_id,
            'Fuel purchase - ' || COALESCE(v_supplier_name, 'Supplier'),
            v_category_id, NEW.amount, COALESCE(NEW.reference, v_reference),
            'Supplier invoice #' || NEW.receipt_id,
            NEW.receipt_id, NEW.id, NEW.payment_date, NEW.paid_by, TRUE,
            NEW.payment_channel);
    PERFORM fn_log_activity(v_company_id, v_branch_id, NEW.paid_by,
        'supplier_payment', 'Payment made against supplier invoice',
        NEW.amount, 'supplier_payments', NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
