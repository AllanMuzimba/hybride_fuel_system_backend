from datetime import date, datetime, timedelta, timezone
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
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
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


@router.put("/{customer_id}", response_model=schemas.CustomerOut)
def update_customer(
    customer_id: int,
    payload: schemas.CustomerUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    customer = db.query(models.Customer).filter(
        models.Customer.id == customer_id,
        models.Customer.company_id == current_user.company_id,
    ).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if not payload.is_active and customer.current_balance != 0:
        raise HTTPException(status_code=409, detail="Settle the account balance before deactivating")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, key, value)
    db.commit()
    db.refresh(customer)
    return customer


@router.delete("/{customer_id}", status_code=204)
def deactivate_customer(
    customer_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    customer = db.query(models.Customer).filter(
        models.Customer.id == customer_id,
        models.Customer.company_id == current_user.company_id,
    ).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")
    if customer.current_balance != 0:
        raise HTTPException(status_code=409, detail="Settle the account balance before deactivating")
    customer.is_active = False
    db.commit()


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
    current_user: models.User = Depends(auth.require_role("attendant", "supervisor", "manager", "developer")),
):
    """Record cash/EFT received from a credit customer. Reduces their balance
    (stored as a negative amount on the ledger by convention) and, if
    branch_id is given, counts as that branch's cash income for the day."""
    customer = db.query(models.Customer).get(payload.customer_id)
    if not customer or customer.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Customer not found")
    if payload.client_request_id:
        existing = db.query(models.CustomerTransaction).filter(
            models.CustomerTransaction.client_request_id == payload.client_request_id,
            models.CustomerTransaction.txn_type == "payment",
        ).first()
        if existing:
            if existing.recorded_by != current_user.id or existing.customer_id != payload.customer_id:
                raise HTTPException(status_code=409, detail="Payment request ID belongs to another payment")
            return existing
    if not customer.is_active:
        raise HTTPException(status_code=409, detail="Customer account is inactive")
    if payload.amount <= 0:
        raise HTTPException(status_code=400, detail="Payment amount must be positive")
    if payload.recorded_at is not None:
        if payload.recorded_at.tzinfo is None:
            raise HTTPException(status_code=422, detail="recorded_at needs a timezone")
        now = datetime.now(timezone.utc)
        if payload.recorded_at > now + timedelta(minutes=5):
            raise HTTPException(status_code=409, detail="Offline payment time is outside the allowed window")

    branch_id = payload.branch_id
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
    elif current_user.branch_id is not None:
        branch_id = current_user.branch_id  # default to the attendant's own branch
    else:
        raise HTTPException(status_code=400, detail="branch_id is required (your account isn't tied to a single branch)")

    txn = models.CustomerTransaction(
        client_request_id=payload.client_request_id,
        transaction_date=payload.recorded_at,
        customer_id=payload.customer_id,
        branch_id=branch_id,
        txn_type="payment",
        amount=-abs(payload.amount),
        balance_after=0,  # overwritten by the before-insert trigger
        recorded_by=current_user.id,
        notes=payload.notes,
        payment_channel=payload.payment_channel,
    )
    db.add(txn)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(txn)
    return txn
