from datetime import date, datetime, time
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models, auth
from ..database import get_db

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/activity-log")
def activity_log(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    activity_type: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """HQ audit trail: every sale, receipt, supplier/customer payment,
    expense and price change across your company (or one branch), newest
    first. activity_type filters to one kind: 'sale' | 'receipt' |
    'supplier_payment' | 'customer_payment' | 'expense' | 'price_change'.
    Backed by get_activity_log_range()."""
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    rows = db.execute(
        text("SELECT * FROM get_activity_log_range(:start, :end, :company_id, :branch_id, :activity_type)"),
        {
            "start": start_dt, "end": end_dt, "company_id": current_user.company_id,
            "branch_id": branch_id, "activity_type": activity_type,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/daily-fuel")
def daily_fuel_report(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Volume report per branch per fuel type per day, for any date range:
    opening stock, received, sold (total), credit portion of sold, closing
    stock — plus the matching purchase/sales money amounts. Always scoped
    to your own company; optionally to one branch. Backed by
    get_fuel_report_range(), which reads from daily_fuel_reports — the
    table the triggers keep up to date."""
    rows = db.execute(
        text("SELECT * FROM get_fuel_report_range(:start, :end, :company_id, :branch_id, :fuel_type_id)"),
        {
            "start": start_date, "end": end_date, "company_id": current_user.company_id,
            "branch_id": branch_id, "fuel_type_id": fuel_type_id,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/financial")
def financial_report(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Cash-basis financial report per branch per day: opening balance,
    income (cash/card/mobile-money sales + debt collected that day),
    expenses, closing balance. Backed by get_financial_report_range()."""
    rows = db.execute(
        text("SELECT * FROM get_financial_report_range(:start, :end, :company_id, :branch_id)"),
        {"start": start_date, "end": end_date, "company_id": current_user.company_id, "branch_id": branch_id},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/financial/company-totals")
def company_financial_totals(
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Company-wide rollup per day: every branch's income/expenses summed,
    plus company-wide (head-office) expenses that aren't tied to one branch.
    There's no single combined opening/closing balance here since each
    branch keeps its own till — see /reports/financial for that, per branch."""
    rows = db.execute(
        text(
            "SELECT * FROM v_company_daily_totals "
            "WHERE company_id = :company_id AND summary_date BETWEEN :start AND :end "
            "ORDER BY summary_date"
        ),
        {"company_id": current_user.company_id, "start": start_date, "end": end_date},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/expenses")
def expense_report(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    category_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Expenses for a date range (optionally filtered by branch/category),
    each with its category name attached — includes the auto-generated
    'Fuel Purchases' expenses posted when a supplier invoice gets paid in
    full. Backed by get_expense_report_range()."""
    rows = db.execute(
        text("SELECT * FROM get_expense_report_range(:start, :end, :company_id, :branch_id, :category_id)"),
        {
            "start": start_date, "end": end_date, "company_id": current_user.company_id,
            "branch_id": branch_id, "category_id": category_id,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/credit-fuel")
def credit_fuel_report(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Fuel given to customers on credit for a date range — physically sold,
    but not counted as realized income until the customer pays it off.
    This is the 'pending' report, separate from the cash financial report."""
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    rows = db.execute(
        text("SELECT * FROM get_credit_report_range(:start, :end, :company_id, :branch_id, :customer_id)"),
        {
            "start": start_dt, "end": end_dt, "company_id": current_user.company_id,
            "branch_id": branch_id, "customer_id": customer_id,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


@router.get("/full-summary")
def full_summary(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Everything balanced together for a date range, scoped to your company
    (optionally one branch): fuel volumes (by branch + type), the cash
    financial summary, and the credit/pending fuel given — so daily-to-
    date-range fuel movement reconciles whether it was sold for cash or
    extended on credit."""
    fuel_rows = db.execute(
        text("SELECT * FROM get_fuel_report_range(:start, :end, :company_id, :branch_id, NULL)"),
        {"start": start_date, "end": end_date, "company_id": current_user.company_id, "branch_id": branch_id},
    ).mappings().all()
    financial_rows = db.execute(
        text("SELECT * FROM get_financial_report_range(:start, :end, :company_id, :branch_id)"),
        {"start": start_date, "end": end_date, "company_id": current_user.company_id, "branch_id": branch_id},
    ).mappings().all()
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date, time.max)
    credit_rows = db.execute(
        text("SELECT * FROM get_credit_report_range(:start, :end, :company_id, :branch_id, NULL)"),
        {"start": start_dt, "end": end_dt, "company_id": current_user.company_id, "branch_id": branch_id},
    ).mappings().all()

    total_credit_outstanding_now = db.execute(
        text("SELECT COALESCE(SUM(current_balance), 0) AS total FROM v_customers_owing WHERE company_id = :company_id"),
        {"company_id": current_user.company_id},
    ).scalar()

    return {
        "start_date": start_date,
        "end_date": end_date,
        "branch_id": branch_id,
        "fuel": [dict(r) for r in fuel_rows],
        "financial": [dict(r) for r in financial_rows],
        "credit_fuel_given": [dict(r) for r in credit_rows],
        "total_customer_debt_outstanding_today": float(total_credit_outstanding_now),
    }


@router.post("/refresh")
def refresh_reports(
    start_date: date,
    end_date: date,
    branch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Force-recompute daily_fuel_reports and daily_financial_summary for a
    date range — use after loading historical data or correcting a
    backdated receipt/sale/expense. Restricted to your own company's
    branch(es); omit branch_id to refresh every branch of your company."""
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
        db.execute(text("SELECT fn_refresh_report_range(:start, :end, :branch_id)"),
                   {"start": start_date, "end": end_date, "branch_id": branch_id})
    else:
        for branch in db.query(models.CompanyBranch).filter(models.CompanyBranch.company_id == current_user.company_id):
            db.execute(text("SELECT fn_refresh_report_range(:start, :end, :branch_id)"),
                       {"start": start_date, "end": end_date, "branch_id": branch.id})
    db.commit()
    return {"status": "refreshed", "start_date": start_date, "end_date": end_date, "branch_id": branch_id}
