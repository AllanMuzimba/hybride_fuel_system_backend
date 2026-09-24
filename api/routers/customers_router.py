from datetime import date
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/customers", tags=["customer accounts"])


@router.post("", response_model=schemas.CustomerOut)
def register_customer(
    payload: schemas.CustomerCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    customer = models.Customer(**payload.model_dump(), company_id=current_user.company_id, registered_by=current_user.id)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@router.get("", response_model=List[schemas.CustomerOut])
def list_customers(
    active_only: bool = True,
    owing_only: bool = False,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    q = db.query(models.Customer).filter(models.Customer.company_id == current_user.company_id)
    if active_only:
        q = q.filter(models.Customer.is_active.is_(True))
    if owing_only:
        q = q.filter(models.Customer.current_balance > 0)
    return q.order_by(models.Customer.name).all()


@router.get("/{customer_id}", response_model=schemas.CustomerOut)
def get_customer(customer_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    customer = db.query(models.Customer).get(customer_id)
    if not customer or customer.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Customer not found")
    return customer


@router.get("/{customer_id}/statement", response_model=List[schemas.CustomerTransactionOut])
def customer_statement(
    customer_id: int,
    start_date: date,
    end_date: date,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Fill-ups, payments and adjustments for one customer over a date range
    (uses the get_customer_statement() database function)."""
    customer = db.query(models.Customer).get(customer_id)
    if not customer or customer.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Customer not found")

    rows = db.execute(
        text("SELECT * FROM get_customer_statement(:cid, :start, :end)"),
        {"cid": customer_id, "start": start_date, "end": end_date},
    ).mappings().all()
    return [dict(r) for r in rows]


@router.post("/payments", response_model=schemas.CustomerTransactionOut)
def record_customer_payment(
    payload: schemas.CustomerPaymentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    """Record cash/EFT received from a credit customer. Reduces their balance
    (stored as a negative amount on the ledger by convention) and, if
    branch_id is given, counts as that branch's cash income for the day."""
    customer = db.query(models.Customer).get(payload.customer_id)
    if not customer or customer.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be positive")

    branch_id = payload.branch_id
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
    elif current_user.branch_id is not None:
        branch_id = current_user.branch_id  # default to the attendant's own branch
    else:
        raise HTTPException(status_code=400, detail="branch_id is required (your account isn't tied to a single branch)")

    txn = models.CustomerTransaction(
        customer_id=payload.customer_id,
        branch_id=branch_id,
        txn_type="payment",
        amount=-abs(payload.amount),
        balance_after=0,  # overwritten by the before-insert trigger
        recorded_by=current_user.id,
        notes=payload.notes,
    )
    db.add(txn)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(txn)
    return txn
