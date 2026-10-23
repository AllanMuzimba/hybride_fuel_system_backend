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
    current_user: models.User = Depends(auth.require_role("manager", "developer", "secretary")),
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


@router.put("/categories/{category_id}", response_model=schemas.ExpenseCategoryOut)
def update_category(
    category_id: int,
    payload: schemas.ExpenseCategoryUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer", "secretary")),
):
    category = db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.id == category_id,
        models.ExpenseCategory.company_id == current_user.company_id,
    ).first()
    if category is None:
        raise HTTPException(status_code=404, detail="Expense category not found")
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    duplicate = db.query(models.ExpenseCategory).filter(
        models.ExpenseCategory.company_id == current_user.company_id,
        models.ExpenseCategory.name == name,
        models.ExpenseCategory.id != category_id,
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Category already exists")
    category.name = name
    db.commit()
    db.refresh(category)
    return category


@router.post("", response_model=schemas.ExpenseOut)
def record_expense(
    payload: schemas.ExpenseCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer", "secretary")),
):
    """Record a manual expense (rent, salaries, maintenance, etc). Leave
    branch_id unset for a company-wide/head-office expense, or set it to
    attribute the cost to one branch's till. Expenses generated automatically
    when a supplier invoice payment is recorded are posted straight to the
    `expenses` table by a database trigger — you don't create those here,
    they'll just show up in the listings/reports with is_system_generated=true."""
    category = db.query(models.ExpenseCategory).get(payload.category_id)
    if not category or category.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Expense category not found")
    if payload.client_request_id:
        existing = db.query(models.Expense).filter(
            models.Expense.client_request_id == payload.client_request_id
        ).first()
        if existing:
            if existing.recorded_by != current_user.id or existing.company_id != current_user.company_id:
                raise HTTPException(status_code=409, detail="Expense request ID belongs to another expense")
            return existing

    if payload.branch_id is not None:
        auth.verify_branch_access(current_user, payload.branch_id, db)

    expense = models.Expense(
        client_request_id=payload.client_request_id,
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
        payment_channel=payload.payment_channel,
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
    current_user: models.User = Depends(auth.require_role("manager", "developer", "secretary")),
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
