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
    as the branch. The receipt doubles as the supplier's invoice
    (invoice_reference = delivery note / invoice number, payment_status
    starts 'unpaid' → shows in v_outstanding_supplier_invoices).

    If dip_before_cm and dip_after_cm are both supplied, the delivered
    volume is calculated by the database from the branch's tank calibration
    (fn_dip_to_volume(after) − fn_dip_to_volume(before)) and takes
    precedence over the submitted volume."""
    auth.verify_branch_access(current_user, payload.branch_id, db)
    if payload.client_request_id:
        existing = db.query(models.FuelReceipt).filter(
            models.FuelReceipt.client_request_id == payload.client_request_id
        ).first()
        if existing:
            if existing.received_by != current_user.id or existing.branch_id != payload.branch_id:
                raise HTTPException(status_code=409, detail="Receipt request ID belongs to another receipt")
            return existing

    supplier = db.query(models.Supplier).get(payload.supplier_id)
    if not supplier or supplier.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Supplier not found in your company")

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in your company")

    volume = payload.volume
    if payload.dip_before_cm is not None and payload.dip_after_cm is not None:
        if payload.dip_after_cm <= payload.dip_before_cm:
            raise HTTPException(
                status_code=400,
                detail="Dip reading after delivery must be greater than the reading before it",
            )
        volume = _volume_from_dips(db, payload.branch_id, payload.fuel_type_id,
                                   payload.dip_before_cm, payload.dip_after_cm)

    if volume is None or volume <= 0:
        raise HTTPException(status_code=400, detail="Volume received must be greater than zero")

    receipt = models.FuelReceipt(
        client_request_id=payload.client_request_id,
        branch_id=payload.branch_id,
        supplier_id=payload.supplier_id,
        fuel_type_id=payload.fuel_type_id,
        volume=volume,
        cost_per_liter=payload.cost_per_liter,
        invoice_reference=payload.invoice_reference,
        received_date=payload.received_date or date.today(),
        received_by=current_user.id,
        dip_before_cm=payload.dip_before_cm,
        dip_after_cm=payload.dip_after_cm,
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


def _volume_from_dips(db: Session, branch_id: int, fuel_type_id: int,
                      dip_before_cm: float, dip_after_cm: float) -> Optional[float]:
    """Litres delivered = (after reading) − (before reading), each converted
    with the branch's tank calibration. NULL when the tank has no capacity
    set yet."""
    row = db.execute(
        text(
            "SELECT fn_dip_to_volume(:b, :f, :after) - fn_dip_to_volume(:b, :f, :before) AS v"
        ),
        {"b": branch_id, "f": fuel_type_id, "after": dip_after_cm, "before": dip_before_cm},
    ).first()
    if row is None or row[0] is None:
        raise HTTPException(
            status_code=422,
            detail="This tank has no calibration yet — set its capacity "
                    "(PATCH /fuel-types/stock/capacity) or enter the volume directly",
        )
    return float(row[0])


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
    return _with_payment_info(db, q.order_by(models.FuelReceipt.received_date.desc()).all())


def _with_payment_info(db: Session, receipts: List[models.FuelReceipt]) -> List[dict]:
    """One extra query adds each invoice's newest payment (reference + notes),
    so the Suppliers audit table can show what the money left the bank with
    without an N+1 round trip per row."""
    if not receipts:
        return []
    latest = (
        db.query(
            models.SupplierPayment.receipt_id,
            models.SupplierPayment.reference,
            models.SupplierPayment.notes,
        )
        .filter(models.SupplierPayment.receipt_id.in_([r.id for r in receipts]))
        .order_by(
            models.SupplierPayment.payment_date.desc(),
            models.SupplierPayment.id.desc(),
        )
        .all()
    )
    newest: dict = {}
    for receipt_id, reference, notes in latest:
        newest.setdefault(receipt_id, (reference, notes))

    rows: List[dict] = []
    for receipt in receipts:
        payload = schemas.FuelReceiptOut.model_validate(receipt).model_dump()
        reference, notes = newest.get(receipt.id, (None, None))
        payload["payment_reference"] = reference
        payload["payment_notes"] = notes
        rows.append(payload)
    return rows


@router.get("/{receipt_id}", response_model=schemas.FuelReceiptOut)
def get_receipt(receipt_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    receipt = db.query(models.FuelReceipt).get(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    auth.verify_branch_access(current_user, receipt.branch_id, db)
    return _with_payment_info(db, [receipt])[0]


@router.post("/payments", response_model=schemas.SupplierPaymentOut)
def pay_supplier_invoice(
    payload: schemas.SupplierPaymentCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("secretary", "manager", "director", "developer")),
):
    """Record a payment against a supplier's invoice (referenced by receipt_id).
    The receipt's payment_status flips to partial/paid automatically, and a
    payment auto-generates a matching expense, including partial payments."""
    receipt = db.query(models.FuelReceipt).get(payload.receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    auth.verify_branch_access(current_user, receipt.branch_id, db)
    if payload.client_request_id:
        existing = db.query(models.SupplierPayment).filter(
            models.SupplierPayment.client_request_id == payload.client_request_id
        ).first()
        if existing:
            if existing.paid_by != current_user.id or existing.receipt_id != payload.receipt_id:
                raise HTTPException(status_code=409, detail="Payment request ID belongs to another payment")
            return existing
    if payload.amount <= 0 or payload.amount > float(receipt.total_cost - receipt.amount_paid):
        raise HTTPException(status_code=400, detail="Payment must be positive and no greater than the invoice balance")

    payment = models.SupplierPayment(
        client_request_id=payload.client_request_id,
        receipt_id=payload.receipt_id,
        amount=payload.amount,
        payment_date=payload.payment_date or date.today(),
        paid_by=current_user.id,
        notes=payload.notes,
        reference=payload.reference,
        payment_channel=payload.payment_channel,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/{receipt_id}/payments", response_model=List[schemas.SupplierPaymentOut])
def list_receipt_payments(
    receipt_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Every payment recorded against one supplier invoice, newest first —
    the Suppliers -> Invoices tab shows these under the confirmed payments
    so a reference (bank transfer / EFT / cheque) is always traceable."""
    receipt = db.query(models.FuelReceipt).get(receipt_id)
    if not receipt:
        raise HTTPException(status_code=404, detail="Receipt not found")
    auth.verify_branch_access(current_user, receipt.branch_id, db)
    return (
        db.query(models.SupplierPayment)
        .filter(models.SupplierPayment.receipt_id == receipt_id)
        .order_by(models.SupplierPayment.payment_date.desc(), models.SupplierPayment.id.desc())
        .all()
    )


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
