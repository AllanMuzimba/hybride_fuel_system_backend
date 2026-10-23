"""
Pydantic schemas used by the API — these are what the Flutter app
actually sends/receives as JSON.
"""
from datetime import date, datetime, time
from typing import Annotated, List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------- auth ----
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    user_id: int
    full_name: str
    company_id: int
    branch_id: Optional[int] = None


class LoginRequest(BaseModel):
    username: str  # accepts username, email address or phone
    password: str


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=150)


class PasswordResetComplete(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    email: Optional[str] = Field(default=None, max_length=150)
    phone: Optional[str] = Field(default=None, max_length=30)
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


# ------------------------------------------------------------ companies ----
class CompanyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    branch_count: int = Field(default=1, ge=1, le=500)
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    url: Optional[str] = None
    branch_names: Optional[List[Annotated[str, Field(min_length=2, max_length=150)]]] = Field(
        default=None, max_length=100
    )


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    slug: str
    url: Optional[str] = None
    is_active: bool
    needs_confirmation: bool
    confirmed_at: Optional[datetime] = None
    registered_by: Optional[int] = None
    created_at: datetime


class CompanyRegistrationRequestCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=150)
    address: str = Field(min_length=2)
    branch_count: int = Field(ge=1, le=500)
    phone: str = Field(min_length=5, max_length=30)
    email: str = Field(min_length=5, max_length=150)
    message: str = Field(min_length=5, max_length=3000)


class CompanyRegistrationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_name: str
    address: str
    branch_count: int
    phone: str
    email: str
    message: str
    status: str
    company_id: Optional[int] = None
    created_at: datetime


class BranchCreate(BaseModel):
    branch_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    company_id: Optional[int] = None


class BranchUpdate(BaseModel):
    """Partial update of a branch (PATCH /branches/{branch_id}) — only the
    fields present in the payload are applied."""
    branch_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: Optional[bool] = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    company_name: Optional[str] = None
    branch_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------- users ----
UserRole = Literal["attendant", "supervisor", "manager", "director", "developer",
                   "dev_support", "secretary"]


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=150)
    role: UserRole = "attendant"
    branch_id: Optional[int] = None  # None = company-wide access
    phone: Optional[str] = None
    email: Optional[str] = None
    company_id: Optional[int] = None  # platform developer only


class UserUpdate(BaseModel):
    username: Optional[str] = Field(default=None, min_length=2, max_length=50)
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    role: Optional[UserRole] = None
    branch_id: Optional[int] = None
    company_id: Optional[int] = None
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=150)
    is_active: Optional[bool] = None
    new_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    branch_id: Optional[int] = None
    username: str
    full_name: str
    role: str
    phone: Optional[str] = None
    email: Optional[str] = None
    is_active: bool
    created_at: datetime


class CompanyInvoiceCreate(BaseModel):
    company_id: int
    description: str = Field(min_length=2, max_length=255)
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    issued_at: date
    due_at: date
    notes: Optional[str] = None


class CompanyInvoiceStatusUpdate(BaseModel):
    status: Literal["unpaid", "paid", "void"]


class CompanyInvoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    invoice_number: str
    description: str
    amount: float
    currency: str
    status: str
    issued_at: date
    due_at: date
    notes: Optional[str] = None
    created_at: datetime


class CompanyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    address: Optional[str] = None
    phone: Optional[str] = Field(default=None, max_length=30)
    email: Optional[str] = Field(default=None, max_length=150)
    is_active: Optional[bool] = None


# ------------------------------------------------------------ suppliers ----
class SupplierCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    vat_number: Optional[str] = None


class SupplierUpdate(BaseModel):
    """Partial update of a supplier account (PUT /suppliers/{id})."""
    name: Optional[str] = None
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    vat_number: Optional[str] = None
    is_active: Optional[bool] = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    vat_number: Optional[str] = None
    is_active: bool
    created_at: datetime


# ------------------------------------------------------------ fuel types ----
class FuelTypeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    default_cost: float = Field(ge=0)
    default_sell_price: float = Field(ge=0)
    company_id: Optional[int] = None  # developer / dev_support only


class FuelTypeUpdate(BaseModel):
    """Updates a fuel type's name and/or HQ reference price. Branch selling
    prices change through PUT /fuel-types/{id}/branch-prices."""
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    default_cost: Optional[float] = Field(default=None, ge=0)
    default_sell_price: Optional[float] = Field(default=None, ge=0)


class FuelTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    name: str
    default_cost: float
    default_sell_price: float
    updated_at: datetime


class BranchFuelPriceUpdate(BaseModel):
    branch_id: int
    fuel_type_id: int
    current_cost: Optional[float] = None
    sell_price: Optional[float] = None


class BranchPriceApply(BaseModel):
    """Reprice one fuel type across many branches at once
    (PUT /fuel-types/{fuel_type_id}/branch-prices)."""
    current_cost: Optional[float] = None
    sell_price: Optional[float] = None
    branch_ids: Optional[List[int]] = None

    @model_validator(mode="after")
    def _require_cost_or_price(self) -> "BranchPriceApply":
        if self.current_cost is None and self.sell_price is None:
            raise ValueError("Provide current_cost, sell_price or both")
        return self


class BranchFuelPriceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    branch_id: int
    company_id: int
    branch_name: str
    fuel_type_id: int
    fuel_type_name: str
    current_cost: float
    sell_price: float
    default_cost: float
    default_sell_price: float
    updated_at: datetime
    updated_by: Optional[int] = None


class FuelStockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    branch_id: int
    fuel_type_id: int
    current_volume: float
    capacity: Optional[float] = None
    updated_at: datetime


class StockCapacityUpdate(BaseModel):
    branch_id: int
    fuel_type_id: int
    capacity: float
    tank_height_cm: Optional[float] = None  # calibrated dip-stick height of that tank
    company_id: Optional[int] = None  # developer / dev_support: build a tank for another tenant


class BranchStockStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    branch_id: int
    company_id: int
    branch_name: str
    fuel_type_id: int
    fuel_type_name: str
    current_volume: float
    capacity: Optional[float] = None
    tank_height_cm: Optional[float] = None
    percent_full: Optional[float] = None
    updated_at: datetime


class CompanyStockTotalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    company_id: int
    fuel_type_id: int
    fuel_type_name: str
    total_current_volume: float
    total_capacity: Optional[float] = None
    percent_full: Optional[float] = None


# --------------------------------------------------------- fuel receipts ----
class FuelReceiptCreate(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=64)
    branch_id: int
    supplier_id: int
    fuel_type_id: int
    volume: float
    cost_per_liter: float
    invoice_reference: str
    received_date: Optional[date] = None
    dip_before_cm: Optional[float] = None   # stick reading before the delivery
    dip_after_cm: Optional[float] = None    # stick reading after the delivery
    notes: Optional[str] = None


class FuelReceiptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    branch_id: int
    supplier_id: int
    fuel_type_id: int
    volume: float
    cost_per_liter: float
    total_cost: float
    invoice_reference: str
    payment_status: str
    amount_paid: float
    received_date: date
    received_by: int
    dip_before_cm: Optional[float] = None
    dip_after_cm: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime
    # latest payment booked against this invoice (Suppliers audit table)
    payment_reference: Optional[str] = None
    payment_notes: Optional[str] = None


# ---------------------------------------------------- dip calibration ----
class DipVolumeOut(BaseModel):
    branch_id: int
    fuel_type_id: int
    dip_cm: float
    volume_litres: float
    source: Literal["calibration", "linear"]


class DipCalibrationPoint(BaseModel):
    dip_cm: float
    volume_litres: float


class DipCalibrationUpdate(BaseModel):
    branch_id: int
    fuel_type_id: int
    points: List[DipCalibrationPoint]
    company_id: Optional[int] = None  # developer / dev_support: calibrate another tenant's tank


class DipCalibrationOut(BaseModel):
    branch_id: int
    fuel_type_id: int
    tank_height_cm: Optional[float] = None
    capacity: Optional[float] = None
    source: Literal["calibration", "linear"]
    points: List[DipCalibrationPoint]


class SupplierPaymentCreate(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=64)
    receipt_id: int
    amount: float
    payment_date: Optional[date] = None
    notes: Optional[str] = None
    reference: Optional[str] = None  # bank transfer / EFT / cheque reference
    payment_channel: Literal["cash", "card", "mobile_money", "bank"] = "bank"


class SupplierPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_id: int
    amount: float
    payment_date: date
    paid_by: int
    notes: Optional[str] = None
    reference: Optional[str] = None
    payment_channel: str = "unknown"
    created_at: datetime


# ------------------------------------------------------------- customers ----
class CustomerCreate(BaseModel):
    company_name: Optional[str] = None
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    monthly_limit: float = Field(default=0, ge=0)


class CustomerUpdate(BaseModel):
    company_name: Optional[str] = None
    name: str = Field(min_length=1)
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    monthly_limit: float = Field(ge=0)
    is_active: bool = True


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    company_name: Optional[str] = None
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    monthly_limit: float
    current_balance: float
    is_active: bool
    created_at: datetime


class CustomerPaymentCreate(BaseModel):
    """A cash/EFT payment received from a credit customer, reducing their balance."""
    customer_id: int
    client_request_id: Optional[str] = Field(default=None, max_length=64)
    recorded_at: Optional[datetime] = None
    branch_id: Optional[int] = None  # which branch's till received the cash
    amount: float
    notes: Optional[str] = None
    payment_channel: Literal["cash", "card", "mobile_money", "bank"] = "cash"


class CustomerTransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_id: int
    branch_id: Optional[int] = None
    txn_type: str
    sale_id: Optional[int] = None
    amount: float
    balance_after: float
    recorded_by: int
    transaction_date: datetime
    notes: Optional[str] = None
    payment_channel: str = "unknown"


# ----------------------------------------------------------------- sales ----
PaymentMethod = Literal["cash", "card", "mobile_money", "account"]


class SaleCreate(BaseModel):
    branch_id: int
    fuel_type_id: int
    customer_id: Optional[int] = None
    volume: float
    payment_method: PaymentMethod
    notes: Optional[str] = None
    client_request_id: Optional[str] = Field(default=None, max_length=64)
    shift_id: Optional[int] = None
    recorded_at: Optional[datetime] = None


class SaleOut(BaseModel):
    """A sale carries its own shift period stamp (shift_id + label + time
    window), so every shift keeps its own set of records even when sales from
    several shifts are listed together."""

    model_config = ConfigDict(from_attributes=True)
    id: int
    branch_id: int
    fuel_type_id: int
    customer_id: Optional[int] = None
    shift_id: Optional[int] = None
    shift_label: Optional[str] = None      # 'Morning' / 'Night' / 'Custom'
    shift_date: Optional[date] = None      # the shift's operating day
    shift_period: Optional[str] = None     # '06:00-14:00'
    volume: float
    cost_price: float
    sell_price: float
    total_amount: float
    payment_method: str
    served_by: int
    sale_date: datetime
    entry_source: str = "realtime"
    notes: Optional[str] = None


# --------------------------------------------------------------- kiosk ----
class ProductCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class KioskProductCreate(BaseModel):
    branch_id: int
    category_id: int
    name: str = Field(min_length=1, max_length=150)
    cost_price: float = Field(ge=0)
    sell_price: float = Field(ge=0)
    stock_quantity: float = Field(default=0, ge=0)


class KioskProductUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    cost_price: Optional[float] = Field(default=None, ge=0)
    sell_price: Optional[float] = Field(default=None, ge=0)
    stock_quantity: Optional[float] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class KioskCartItem(BaseModel):
    product_id: Optional[int] = None
    fuel_type_id: Optional[int] = None
    quantity: float = Field(gt=0)

    @model_validator(mode="after")
    def exactly_one_item_type(self):
        if (self.product_id is None) == (self.fuel_type_id is None):
            raise ValueError("Choose either a product or fuel type")
        return self


class KioskSaleCreate(BaseModel):
    branch_id: int
    payment_method: PaymentMethod
    cash_received: Optional[float] = Field(default=None, ge=0)
    items: List[KioskCartItem] = Field(min_length=1)


class CompanySalesSettingsUpdate(BaseModel):
    company_id: int
    kiosk_enabled: bool = True
    kiosk_sell_roles: List[UserRole]
    kiosk_view_roles: List[UserRole]


# ---------------------------------------------------------------- shifts ----
class ShiftTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    start_time: time
    end_time: time


class ShiftTemplateOut(ShiftTemplateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    is_active: bool
    usage_count: int = 0


EntryMode = Literal["realtime", "end_of_shift"]
ShiftStatus = Literal["open", "closed"]


class ShiftFuelReadingInput(BaseModel):
    fuel_type_id: int
    manual_volume: Optional[float] = Field(default=None, ge=0)
    dip_cm: Optional[float] = Field(default=None, ge=0)


class ShiftFuelReadingOut(BaseModel):
    fuel_type_id: int
    fuel_name: str
    opening_system_volume: float
    opening_manual_volume: Optional[float] = None
    opening_dip_cm: Optional[float] = None
    opening_variance: Optional[float] = None
    closing_system_volume: Optional[float] = None
    closing_manual_volume: Optional[float] = None
    closing_dip_cm: Optional[float] = None
    closing_variance: Optional[float] = None


class ShiftCreate(BaseModel):
    branch_id: int
    shift_date: Optional[date] = None          # defaults to today
    shift_label: str = "Custom"                # 'Morning', 'Night', 'Custom', ...
    start_time: time
    end_time: time
    # None = keep the branch's usual way of recording sales: branches that
    # don't support realtime sales inherit 'end_of_shift' from their previous
    # shift, branches that sell live inherit 'realtime'.
    entry_mode: Optional[EntryMode] = None
    notes: Optional[str] = None
    opening_readings: List[ShiftFuelReadingInput] = Field(default_factory=list)


class ShiftCloseCreate(BaseModel):
    closing_readings: List[ShiftFuelReadingInput] = Field(min_length=1)
    cash_received: float = Field(ge=0)


class ShiftLeaveCreate(BaseModel):
    cash_received: Optional[float] = Field(default=None, ge=0)


class ShiftModeUpdate(BaseModel):
    entry_mode: EntryMode


class ShiftAttendeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: int
    username: str
    full_name: str
    role: str
    joined_at: datetime
    left_at: Optional[datetime] = None
    system_cash_amount: Optional[float] = None
    counted_cash_amount: Optional[float] = None
    cash_variance: Optional[float] = None


class ShiftOut(BaseModel):
    id: int
    company_id: int
    branch_id: int
    branch_name: str
    shift_date: date
    shift_label: str
    start_time: time
    end_time: time
    entry_mode: str
    status: str
    opened_by: int
    opened_by_name: str
    opened_at: datetime
    closed_by: Optional[int] = None
    closed_at: Optional[datetime] = None
    notes: Optional[str] = None
    attendees: List[ShiftAttendeeOut] = Field(default_factory=list)
    sale_count: int = 0
    realtime_volume: float = 0.0
    realtime_amount: float = 0.0
    entry_volume: float = 0.0
    entry_amount: float = 0.0
    total_volume: float = 0.0
    total_amount: float = 0.0
    cash_amount: float = 0.0
    card_amount: float = 0.0
    mobile_money_amount: float = 0.0
    account_amount: float = 0.0
    current_user_joined: bool = False
    fuel_readings: List[ShiftFuelReadingOut] = Field(default_factory=list)
    system_cash_amount: Optional[float] = None
    counted_cash_amount: Optional[float] = None
    cash_variance: Optional[float] = None


class ShiftVolumeEntry(BaseModel):
    fuel_type_id: int
    volume: float = Field(gt=0)
    payment_method: PaymentMethod = "cash"
    customer_id: Optional[int] = None
    notes: Optional[str] = None


class ShiftVolumeEntryCreate(BaseModel):
    entries: List[ShiftVolumeEntry] = Field(min_length=1)
    notes: Optional[str] = None


class ShiftCustomerPurchase(BaseModel):
    fuel_type_id: int
    customer_id: int
    volume: float = Field(gt=0)


class ShiftFuelSettlement(BaseModel):
    fuel_type_id: int
    total_volume: Optional[float] = Field(default=None, gt=0)
    opening_volume: Optional[float] = Field(default=None, ge=0)
    closing_volume: Optional[float] = Field(default=None, ge=0)
    closing_dip_cm: Optional[float] = Field(default=None, ge=0)
    card_volume: float = Field(default=0, ge=0)
    mobile_money_volume: float = Field(default=0, ge=0)


class ShiftSettlementCreate(BaseModel):
    fuel_totals: List[ShiftFuelSettlement] = Field(min_length=1)
    customer_purchases: List[ShiftCustomerPurchase] = Field(default_factory=list)
    cash_received: Optional[float] = Field(default=None, ge=0)


# -------------------------------------------------------------- expenses ----
class ExpenseCategoryCreate(BaseModel):
    name: str


class ExpenseCategoryUpdate(BaseModel):
    name: str


class ExpenseCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    name: str
    is_active: bool


class ExpenseCreate(BaseModel):
    client_request_id: Optional[str] = Field(default=None, max_length=64)
    name: str
    category_id: int
    amount: float
    branch_id: Optional[int] = None  # None = company-wide expense
    reference: Optional[str] = None
    notes: Optional[str] = None
    expense_date: Optional[date] = None
    payment_channel: Literal["cash", "card", "mobile_money", "bank"] = "cash"


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    branch_id: Optional[int] = None
    name: str
    category_id: int
    amount: float
    reference: Optional[str] = None
    notes: Optional[str] = None
    receipt_id: Optional[int] = None
    expense_date: date
    recorded_by: Optional[int] = None
    is_system_generated: bool
    payment_channel: str = "unknown"
    created_at: datetime


# --------------------------------------------------------------- reports ----
class DailyFuelReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    report_date: date
    branch_id: int
    company_id: int
    branch_name: str
    fuel_type_id: int
    fuel_type_name: str
    opening_stock: float
    received_volume: float
    sold_volume: float
    credit_volume: float
    closing_stock: float
    total_purchase_amount: float
    total_sales_amount: float
    credit_amount: float
    generated_at: datetime


class DailyFinancialSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    summary_date: date
    branch_id: int
    company_id: int
    branch_name: str
    opening_balance: float
    cash_sales_income: float
    customer_payments_income: float
    total_income: float
    total_expenses: float
    closing_balance: float
    generated_at: datetime


class ActivityLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    branch_id: Optional[int] = None
    user_id: Optional[int] = None
    activity_type: str
    description: Optional[str] = None
    amount: Optional[float] = None
    reference_table: Optional[str] = None
    reference_id: Optional[int] = None
    created_at: datetime
