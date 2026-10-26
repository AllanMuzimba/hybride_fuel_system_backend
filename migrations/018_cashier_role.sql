-- The cashier sells from the kiosk POS and the Sales screen but never
-- touches stock, reports or shift settlement.
ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'cashier';
