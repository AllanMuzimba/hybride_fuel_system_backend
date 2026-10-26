"""
SQLAlchemy models. These mirror schema.sql exactly — the schema owns the
real constraints/triggers; these classes are for reading and writing rows
through the ORM. Generated columns (total_cost, total_amount) and
trigger-maintained fields are marked read-only from the app's side.
"""
from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Boolean, Numeric, Date, DateTime,
    Time, ForeignKey, Enum, UniqueConstraint, func, Computed, JSON
)
from .database import Base

# ---- Enums (must match the Postgres ENUM types in schema.sql) ----
USER_ROLES = ("attendant", "supervisor", "manager", "director", "developer",
              "dev_support", "secretary", "cashier")
PAYMENT_STATUSES = ("unpaid", "partial", "paid")
PAYMENT_METHODS = ("cash", "card", "mobile_money", "account")
CUSTOMER_TXN_TYPES = ("fill_up", "payment", "adjustment")


class Company(Base):
    __tablename__ = "companies"
    id = Column(Integer, primary_key=True)
    name = Column(String(150), nullable=False)
    address = Column(Text)
    latitude = Column(Numeric(10, 7))
    longitude = Column(Numeric(10, 7))
    phone = Column(String(30))
    email = Column(String(150))
    slug = Column(String(100), unique=True, nullable=False)
    url = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    registered_by = Column(Integer, ForeignKey("users.id"))
    confirmed_by = Column(Integer, ForeignKey("users.id"))
    confirmed_at = Column(DateTime(timezone=True))
    needs_confirmation = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CompanyRegistrationRequest(Base):
    __tablename__ = "company_registration_requests"
    id = Column(Integer, primary_key=True)
    company_name = Column(String(150), nullable=False)
    address = Column(Text, nullable=False)
    branch_count = Column(Integer, nullable=False)
    phone = Column(String(30), nullable=False)
    email = Column(String(150), nullable=False)
    message = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    submitted_by = Column(Integer, ForeignKey("users.id"))
    reviewed_by = Column(Integer, ForeignKey("users.id"))
    company_id = Column(Integer, ForeignKey("companies.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    reviewed_at = Column(DateTime(timezone=True))


class CompanyBranch(Base):
    __tablename__ = "company_branches"
    __table_args__ = (UniqueConstraint("company_id", "branch_name"),)
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_name = Column(String(150), nullable=False)
    phone = Column(String(30))
    address = Column(Text)
    latitude = Column(Numeric(10, 7))
    longitude = Column(Numeric(10, 7))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"))  # NULL = all branches
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(150), nullable=False)
    role = Column(Enum(*USER_ROLES, name="user_role"), nullable=False, default="attendant")
    phone = Column(String(30))
    email = Column(String(150))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserCompanyMembership(Base):
    __tablename__ = "user_company_memberships"
    __table_args__ = (UniqueConstraint("user_id", "company_id"),)
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id", ondelete="SET NULL"))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CompanyInvoice(Base):
    __tablename__ = "company_invoices"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False, index=True)
    invoice_number = Column(String(50), nullable=False, unique=True)
    description = Column(String(255), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    status = Column(String(20), nullable=False, default="unpaid")
    issued_at = Column(Date, nullable=False)
    due_at = Column(Date, nullable=False)
    notes = Column(Text)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(150), nullable=False)
    contact_person = Column(String(150))
    phone = Column(String(30))
    email = Column(String(150))
    address = Column(Text)
    vat_number = Column(String(50))
    is_active = Column(Boolean, nullable=False, default=True)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FuelType(Base):
    __tablename__ = "fuel_types"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(50), nullable=False)
    default_cost = Column(Numeric(12, 2), nullable=False, default=0)        # HQ reference cost
    default_sell_price = Column(Numeric(12, 2), nullable=False, default=0)  # HQ reference sell price
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"))


class BranchFuelPrice(Base):
    """The price actually used when a branch sells this fuel type — seeded
    from FuelType.default_cost/default_sell_price, then moves independently
    per branch (see PATCH /fuel-types/branch-prices)."""
    __tablename__ = "branch_fuel_prices"
    branch_id = Column(Integer, ForeignKey("company_branches.id"), primary_key=True)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), primary_key=True)
    current_cost = Column(Numeric(12, 2), nullable=False, default=0)
    sell_price = Column(Numeric(12, 2), nullable=False, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"))


class FuelStock(Base):
    __tablename__ = "fuel_stock"
    branch_id = Column(Integer, ForeignKey("company_branches.id"), primary_key=True)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), primary_key=True)
    current_volume = Column(Numeric(14, 3), nullable=False, default=0)
    capacity = Column(Numeric(14, 3))  # tank/storage capacity; NULL = not tracked
    tank_height_cm = Column(Numeric(8, 2), nullable=False, default=100)  # calibrated dip-stick height
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class FuelTank(Base):
    __tablename__ = "fuel_tanks"
    __table_args__ = (UniqueConstraint("branch_id", "name"),)
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    name = Column(String(100), nullable=False)
    current_volume = Column(Numeric(14, 3), nullable=False, default=0)
    capacity = Column(Numeric(14, 3), nullable=False)
    tank_height_cm = Column(Numeric(8, 2), nullable=False, default=100)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class TankDipCalibration(Base):
    __tablename__ = "tank_dip_calibrations"
    __table_args__ = (UniqueConstraint("tank_id", "dip_cm"),)
    id = Column(Integer, primary_key=True)
    tank_id = Column(Integer, ForeignKey("fuel_tanks.id"), nullable=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    dip_cm = Column(Numeric(8, 2), nullable=False)
    volume_litres = Column(Numeric(14, 3), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FuelPump(Base):
    __tablename__ = "fuel_pumps"
    __table_args__ = (UniqueConstraint("branch_id", "name"),)
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    tank_id = Column(Integer, ForeignKey("fuel_tanks.id"), nullable=False)
    name = Column(String(100), nullable=False)
    meter_reading = Column(Numeric(16, 3), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FuelReceipt(Base):
    __tablename__ = "fuel_receipts"
    id = Column(Integer, primary_key=True)
    client_request_id = Column(String(64), unique=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    tank_id = Column(Integer, ForeignKey("fuel_tanks.id"))
    volume = Column(Numeric(14, 3), nullable=False)
    cost_per_liter = Column(Numeric(12, 2), nullable=False)
    total_cost = Column(Numeric(14, 2), Computed("volume * cost_per_liter", persisted=True))  # generated column
    invoice_reference = Column(String(100), nullable=False)
    payment_status = Column(Enum(*PAYMENT_STATUSES, name="payment_status"), nullable=False, default="unpaid")
    amount_paid = Column(Numeric(14, 2), nullable=False, default=0)  # trigger-maintained — read only
    received_date = Column(Date, nullable=False)
    received_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    dip_before_cm = Column(Numeric(8, 2))   # dip-stick reading before the delivery
    dip_after_cm = Column(Numeric(8, 2))    # dip-stick reading after the delivery
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    id = Column(Integer, primary_key=True)
    client_request_id = Column(String(64), unique=True)
    receipt_id = Column(Integer, ForeignKey("fuel_receipts.id"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    payment_date = Column(Date, nullable=False)
    paid_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    notes = Column(Text)
    reference = Column(String(100))  # bank transfer / EFT / cheque reference
    payment_channel = Column(String(20), nullable=False, default="unknown")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    company_name = Column(String(150))  # the customer's OWN business name
    name = Column(String(150), nullable=False)
    phone = Column(String(30))
    address = Column(Text)
    email = Column(String(150))
    monthly_limit = Column(Numeric(14, 2), nullable=False, default=0)
    current_balance = Column(Numeric(14, 2), nullable=False, default=0)  # trigger-maintained — read only
    is_active = Column(Boolean, nullable=False, default=True)
    registered_by = Column(Integer, ForeignKey("users.id"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ShiftTemplate(Base):
    __tablename__ = "shift_templates"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(60), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class Shift(Base):
    """One operating period at a branch: the date + time window the forecourt
    ran for, whether sales were captured live ('realtime') or entered as a
    total afterwards ('end_of_shift'), and who opened/closed it."""
    __tablename__ = "shifts"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    shift_date = Column(Date, nullable=False)
    shift_label = Column(String(60), nullable=False, default="Custom")
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    entry_mode = Column(String(20), nullable=False, default="realtime")
    status = Column(String(10), nullable=False, default="open")  # open | closed
    opened_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_by = Column(Integer, ForeignKey("users.id"))
    closed_at = Column(DateTime(timezone=True))
    notes = Column(Text)
    system_cash_amount = Column(Numeric(14, 2))
    counted_cash_amount = Column(Numeric(14, 2))


class ShiftFuelReading(Base):
    """Automatic and physical fuel measurements captured when a shift opens
    and closes. One row per fuel type makes each variance auditable."""
    __tablename__ = "shift_fuel_readings"
    __table_args__ = (UniqueConstraint("shift_id", "fuel_type_id"),)
    id = Column(Integer, primary_key=True)
    shift_id = Column(Integer, ForeignKey("shifts.id", ondelete="CASCADE"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    opening_system_volume = Column(Numeric(14, 3), nullable=False, default=0)
    opening_manual_volume = Column(Numeric(14, 3))
    opening_dip_cm = Column(Numeric(8, 2))
    closing_system_volume = Column(Numeric(14, 3))
    closing_manual_volume = Column(Numeric(14, 3))
    closing_dip_cm = Column(Numeric(8, 2))


class ShiftAttendance(Base):
    """A user signing in to a shift — the attendance trail shown on the
    Shifts page (who was on the forecourt, when they joined/left)."""
    __tablename__ = "shift_attendance"
    id = Column(Integer, primary_key=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True))


class ShiftPumpAssignment(Base):
    __tablename__ = "shift_pump_assignments"
    __table_args__ = (UniqueConstraint("shift_id", "pump_id"),)
    id = Column(Integer, primary_key=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    pump_id = Column(Integer, ForeignKey("fuel_pumps.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opening_meter = Column(Numeric(16, 3), nullable=False)
    closing_meter = Column(Numeric(16, 3))
    assigned_at = Column(DateTime(timezone=True), server_default=func.now())


class Sale(Base):
    __tablename__ = "sales"
    id = Column(Integer, primary_key=True)
    client_request_id = Column(String(64), unique=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    shift_id = Column(Integer, ForeignKey("shifts.id"))  # shift this sale was booked to
    pump_id = Column(Integer, ForeignKey("fuel_pumps.id"))
    volume = Column(Numeric(14, 3), nullable=False)
    cost_price = Column(Numeric(12, 2), nullable=False)
    sell_price = Column(Numeric(12, 2), nullable=False)
    total_amount = Column(Numeric(14, 2), Computed("volume * sell_price", persisted=True))  # generated column
    payment_method = Column(Enum(*PAYMENT_METHODS, name="payment_method"), nullable=False)
    served_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    sale_date = Column(DateTime(timezone=True), server_default=func.now())
    entry_source = Column(String(20), nullable=False, default="realtime")  # realtime | shift_close
    notes = Column(Text)


class FuelVoucher(Base):
    __tablename__ = "fuel_vouchers"
    id = Column(Integer, primary_key=True)
    token = Column(String(20), unique=True, nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    volume = Column(Numeric(14, 3), nullable=False)
    payment_method = Column(Enum(*PAYMENT_METHODS, name="payment_method", create_type=False), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"))
    status = Column(String(20), nullable=False, default="issued")
    issued_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    redeemed_by = Column(Integer, ForeignKey("users.id"))
    pump_id = Column(Integer, ForeignKey("fuel_pumps.id"))
    sale_id = Column(Integer, ForeignKey("sales.id"))
    issued_at = Column(DateTime(timezone=True), server_default=func.now())
    redeemed_at = Column(DateTime(timezone=True))


class ProductCategory(Base):
    __tablename__ = "product_categories"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KioskProduct(Base):
    __tablename__ = "kiosk_products"
    __table_args__ = (UniqueConstraint("branch_id", "name"),)
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("product_categories.id"), nullable=False)
    name = Column(String(150), nullable=False)
    barcode = Column(String(64))   # typed or scanned at the POS
    cost_price = Column(Numeric(12, 2), nullable=False, default=0)
    sell_price = Column(Numeric(12, 2), nullable=False, default=0)
    stock_quantity = Column(Numeric(14, 3), nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class KioskSale(Base):
    __tablename__ = "kiosk_sales"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"))
    payment_method = Column(Enum(*PAYMENT_METHODS, name="payment_method", create_type=False), nullable=False)
    total_amount = Column(Numeric(14, 2), nullable=False)
    cash_received = Column(Numeric(14, 2))
    change_given = Column(Numeric(14, 2))
    served_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    sale_date = Column(DateTime(timezone=True), server_default=func.now())


class KioskSaleItem(Base):
    __tablename__ = "kiosk_sale_items"
    id = Column(Integer, primary_key=True)
    sale_id = Column(Integer, ForeignKey("kiosk_sales.id", ondelete="CASCADE"), nullable=False)
    product_id = Column(Integer, ForeignKey("kiosk_products.id"))
    fuel_sale_id = Column(Integer, ForeignKey("sales.id"))
    item_name = Column(String(150), nullable=False)
    item_type = Column(String(10), nullable=False)
    quantity = Column(Numeric(14, 3), nullable=False)
    unit_cost = Column(Numeric(12, 2), nullable=False)
    unit_price = Column(Numeric(12, 2), nullable=False)
    total_amount = Column(Numeric(14, 2), nullable=False)


class CompanySalesSettings(Base):
    __tablename__ = "company_sales_settings"
    company_id = Column(Integer, ForeignKey("companies.id"), primary_key=True)
    kiosk_enabled = Column(Boolean, nullable=False, default=True)
    kiosk_sell_roles = Column(
        JSON, nullable=False, default=lambda: ["manager", "supervisor", "developer"]
    )
    kiosk_view_roles = Column(
        JSON, nullable=False, default=lambda: ["manager", "supervisor", "developer"]
    )
    updated_by = Column(Integer, ForeignKey("users.id"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class CustomerTransaction(Base):
    __tablename__ = "customer_transactions"
    id = Column(Integer, primary_key=True)
    client_request_id = Column(String(64), unique=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    txn_type = Column(Enum(*CUSTOMER_TXN_TYPES, name="customer_txn_type"), nullable=False)
    sale_id = Column(Integer, ForeignKey("sales.id"))
    amount = Column(Numeric(14, 2), nullable=False)
    balance_after = Column(Numeric(14, 2))  # trigger-set for payment/adjustment — read only on those
    recorded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    transaction_date = Column(DateTime(timezone=True), server_default=func.now())
    notes = Column(Text)
    payment_channel = Column(String(20), nullable=False, default="unknown")


class ExpenseCategory(Base):
    __tablename__ = "expense_categories"
    __table_args__ = (UniqueConstraint("company_id", "name"),)
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    name = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class Expense(Base):
    __tablename__ = "expenses"
    id = Column(Integer, primary_key=True)
    client_request_id = Column(String(64), unique=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"))  # NULL = company-wide expense
    name = Column(String(150), nullable=False)
    category_id = Column(Integer, ForeignKey("expense_categories.id"), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    reference = Column(String(100))
    notes = Column(Text)
    receipt_id = Column(Integer, ForeignKey("fuel_receipts.id"))
    supplier_payment_id = Column(Integer, ForeignKey("supplier_payments.id"))
    expense_date = Column(Date, nullable=False)
    recorded_by = Column(Integer, ForeignKey("users.id"))
    is_system_generated = Column(Boolean, nullable=False, default=False)  # read only — set by trigger
    payment_channel = Column(String(20), nullable=False, default="unknown")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DailyFuelReport(Base):
    __tablename__ = "daily_fuel_reports"
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    report_date = Column(Date, nullable=False)
    fuel_type_id = Column(Integer, ForeignKey("fuel_types.id"), nullable=False)
    opening_stock = Column(Numeric(14, 3), nullable=False, default=0)
    received_volume = Column(Numeric(14, 3), nullable=False, default=0)
    sold_volume = Column(Numeric(14, 3), nullable=False, default=0)
    credit_volume = Column(Numeric(14, 3), nullable=False, default=0)
    closing_stock = Column(Numeric(14, 3), nullable=False, default=0)
    total_purchase_amount = Column(Numeric(14, 2), nullable=False, default=0)
    total_sales_amount = Column(Numeric(14, 2), nullable=False, default=0)
    credit_amount = Column(Numeric(14, 2), nullable=False, default=0)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())


class DailyFinancialSummary(Base):
    __tablename__ = "daily_financial_summary"
    id = Column(Integer, primary_key=True)
    branch_id = Column(Integer, ForeignKey("company_branches.id"), nullable=False)
    summary_date = Column(Date, nullable=False)
    opening_balance = Column(Numeric(14, 2), nullable=False, default=0)
    cash_sales_income = Column(Numeric(14, 2), nullable=False, default=0)
    customer_payments_income = Column(Numeric(14, 2), nullable=False, default=0)
    total_income = Column(Numeric(14, 2), nullable=False, default=0)
    total_expenses = Column(Numeric(14, 2), nullable=False, default=0)
    closing_balance = Column(Numeric(14, 2), nullable=False, default=0)
    generated_at = Column(DateTime(timezone=True), server_default=func.now())


class ActivityLog(Base):
    """Append-only audit trail — HQ's window into what happened at every
    branch. Rows are written only by database triggers; the app never
    inserts here directly."""
    __tablename__ = "activity_log"
    id = Column(BigInteger, primary_key=True)
    company_id = Column(Integer, ForeignKey("companies.id"), nullable=False)
    branch_id = Column(Integer, ForeignKey("company_branches.id"))  # NULL = company-wide action
    user_id = Column(Integer, ForeignKey("users.id"))
    activity_type = Column(String(50), nullable=False)
    description = Column(Text)
    amount = Column(Numeric(14, 2))
    reference_table = Column(String(50))
    reference_id = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
