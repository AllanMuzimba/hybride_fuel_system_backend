from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("/categories", response_model=schemas.ExpenseCategoryOut)
def create_category(
    payload: schemas.ExpenseCategoryCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    if db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.company_id == current_user.company_id,
        models.ExpenseCategory.name == payload.name,
    ).first():
        raise HTTPException(status_code=400, detail="Category already exists for your company")
    category = models.ExpenseCategory(name=payload.name, company_id=current_user.company_id)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.get("/categories", response_model=List[schemas.ExpenseCategoryOut])
def list_categories(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return (
        db.query(models.ExpenseCategory)
        .filter(models.ExpenseCategory.company_id == current_user.company_id, models.ExpenseCategory.is_active.is_(True))
        .order_by(models.ExpenseCategory.name)
        .all()
    )


@router.post("", response_model=schemas.ExpenseOut)
def record_expense(
    payload: schemas.ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    """Record a manual expense (rent, salaries, maintenance, etc). Leave
    branch_id unset for a company-wide/head-office expense, or set it to
    attribute the cost to one branch's till. Expenses generated automatically
    when a supplier invoice is paid in full are posted straight to the
    `expenses` table by a database trigger — you don't create those here,
    they'll just show up in the listings/reports with is_system_generated=true."""
    category = db.query(models.ExpenseCategory).get(payload.category_id)
    if not category or category.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Expense category not found")

    if payload.branch_id is not None:
        auth.verify_branch_access(current_user, payload.branch_id, db)

    expense = models.Expense(
        company_id=current_user.company_id,
        branch_id=payload.branch_id,
        name=payload.name,
        category_id=payload.category_id,
        amount=payload.amount,
        reference=payload.reference,
        notes=payload.notes,
        expense_date=payload.expense_date or date.today(),
        recorded_by=current_user.id,
        is_system_generated=False,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("", response_model=List[schemas.ExpenseOut])
def list_expenses(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    branch_id: Optional[int] = None,
    category_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    q = db.query(models.Expense).filter(models.Expense.company_id == current_user.company_id)
    if start_date:
        q = q.filter(models.Expense.expense_date >= start_date)
    if end_date:
        q = q.filter(models.Expense.expense_date <= end_date)
    if branch_id:
        q = q.filter(models.Expense.branch_id == branch_id)
    if category_id:
        q = q.filter(models.Expense.category_id == category_id)
    return q.order_by(models.Expense.expense_date.desc()).all()
