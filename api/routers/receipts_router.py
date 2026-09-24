from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/receipts", tags=["fuel receipts (supplier deliveries)"])


@router.post("", response_model=schemas.FuelReceiptOut)
def receive_fuel(
    payload: schemas.FuelReceiptCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    """Record fuel received from a supplier at one of your company's branches.
    Stock and the daily report update automatically via database triggers,
    which also confirm the supplier and fuel type belong to the same company
    as the branch."""
    auth.verify_branch_access(current_user, payload.branch_id, db)

    supplier = db.query(models.Supplier).get(payload.supplier_id)
    if not supplier or supplier.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Supplier not found in your company")

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in your company")

    receipt = models.FuelReceipt(
        branch_id=payload.branch_id,
        supplier_id=payload.supplier_id,
        fuel_type_id=payload.fuel_type_id,
        volume=payload.volume,
        cost_per_liter=payload.cost_per_liter,
        invoice_reference=payload.invoice_reference,
        received_date=payload.received_date or date.today(),
        received_by=current_user.id,
        notes=payload.notes,
    )
    db.add(receipt)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(receipt)
    return receipt


@router.get("", response_model=List[schemas.FuelReceiptOut])
def list_receipts(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    branch_id: Optional[int] = None,
    supplier_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    payment_status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    q = db.query(models.FuelReceipt).join(
        models.CompanyBranch, models.CompanyBranch.id == models.FuelReceipt.branch_id
    ).filter(models.CompanyBranch.company_id == current_user.company_id)

    if start_date:
        q = q.filter(models.FuelReceipt.received_date >= start_date)
    if end_date:
        q = q.filter(models.FuelReceipt.received_date <= end_date)
    if branch_id:
        q = q.filter(models.FuelReceipt.branch_id == branch_id)
    if supplier_id:
        q = q.filter(models.FuelReceipt.supplier_id == supplier_id)
    if fuel_type_id:
        q = q.filter(models.FuelReceipt.fuel_type_id == fuel_type_id)
    if payment_status:
        q = q.filter(models.FuelReceipt.payment_status == payment_status)
    return q.order_by(models.FuelReceipt.received_date.desc()).all()


@router.get("/{receipt_id}", response_model=schemas.FuelReceiptOut)
def get_receipt(receipt_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    receipt = db.query(models.FuelReceipt).get(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    auth.verify_branch_access(current_user, receipt.branch_id, db)
    return receipt


@router.post("/payments", response_model=schemas.SupplierPaymentOut)
def pay_supplier_invoice(
    payload: schemas.SupplierPaymentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Record a payment against a supplier's invoice (referenced by receipt_id).
    The receipt's payment_status flips to partial/paid automatically, and a
    'paid' invoice auto-generates a matching expense."""
    receipt = db.query(models.FuelReceipt).get(payload.receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    auth.verify_branch_access(current_user, receipt.branch_id, db)

    payment = models.SupplierPayment(
        receipt_id=payload.receipt_id,
        amount=payload.amount,
        payment_date=payload.payment_date or date.today(),
        paid_by=current_user.id,
        notes=payload.notes,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/payments/outstanding")
def outstanding_invoices(
    branch_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Uses the v_outstanding_supplier_invoices view, scoped to your company."""
    query = "SELECT * FROM v_outstanding_supplier_invoices WHERE company_id = :company_id"
    params = {"company_id": current_user.company_id}
    if branch_id:
        query += " AND branch_id = :branch_id"
        params["branch_id"] = branch_id
    query += " ORDER BY received_date"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]
