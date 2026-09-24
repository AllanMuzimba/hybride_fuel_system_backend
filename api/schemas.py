"""
Pydantic schemas used by the API — these are what the Flutter app
actually sends/receives as JSON.
"""
from datetime import date, datetime
from typing import Optional, Literal
from pydantic import BaseModel, ConfigDict

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
    username: str
    password: str


# ------------------------------------------------------------ companies ----
class CompanyCreate(BaseModel):
    name: str
    slug: str
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    url: Optional[str] = None


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
    created_at: datetime


class BranchCreate(BaseModel):
    branch_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class BranchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    branch_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool
    created_at: datetime


# ---------------------------------------------------------------- users ----
UserRole = Literal["attendant", "supervisor", "manager", "director", "developer"]


class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: UserRole = "attendant"
    branch_id: Optional[int] = None  # None = company-wide access
    phone: Optional[str] = None
    email: Optional[str] = None


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


# ------------------------------------------------------------ suppliers ----
class SupplierCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None


class SupplierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    name: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    is_active: bool
    created_at: datetime


# ------------------------------------------------------------ fuel types ----
class FuelTypeCreate(BaseModel):
    name: str
    default_cost: float
    default_sell_price: float


class FuelTypeUpdate(BaseModel):
    """Updates HQ's reference/default price. Does NOT change any branch's
    actual selling price — use PATCH /fuel-types/branch-prices for that."""
    default_cost: Optional[float] = None
    default_sell_price: Optional[float] = None


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


class BranchStockStatusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    branch_id: int
    company_id: int
    branch_name: str
    fuel_type_id: int
    fuel_type_name: str
    current_volume: float
    capacity: Optional[float] = None
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
    branch_id: int
    supplier_id: int
    fuel_type_id: int
    volume: float
    cost_per_liter: float
    invoice_reference: str
    received_date: Optional[date] = None
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
    notes: Optional[str] = None
    created_at: datetime


class SupplierPaymentCreate(BaseModel):
    receipt_id: int
    amount: float
    payment_date: Optional[date] = None
    notes: Optional[str] = None


class SupplierPaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    receipt_id: int
    amount: float
    payment_date: date
    paid_by: int
    notes: Optional[str] = None
    created_at: datetime


# ------------------------------------------------------------- customers ----
class CustomerCreate(BaseModel):
    company_name: Optional[str] = None
    name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    email: Optional[str] = None
    monthly_limit: float = 0


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
    branch_id: Optional[int] = None  # which branch's till received the cash
    amount: float
    notes: Optional[str] = None


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


# ----------------------------------------------------------------- sales ----
PaymentMethod = Literal["cash", "card", "mobile_money", "account"]


class SaleCreate(BaseModel):
    branch_id: int
    fuel_type_id: int
    customer_id: Optional[int] = None
    volume: float
    payment_method: PaymentMethod
    notes: Optional[str] = None


class SaleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    branch_id: int
    fuel_type_id: int
    customer_id: Optional[int] = None
    volume: float
    cost_price: float
    sell_price: float
    total_amount: float
    payment_method: str
    served_by: int
    sale_date: datetime
    notes: Optional[str] = None


# -------------------------------------------------------------- expenses ----
class ExpenseCategoryCreate(BaseModel):
    name: str


class ExpenseCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    company_id: int
    name: str
    is_active: bool


class ExpenseCreate(BaseModel):
    name: str
    category_id: int
    amount: float
    branch_id: Optional[int] = None  # None = company-wide expense
    reference: Optional[str] = None
    notes: Optional[str] = None
    expense_date: Optional[date] = None


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
