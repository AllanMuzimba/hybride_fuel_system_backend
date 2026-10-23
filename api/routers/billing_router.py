from datetime import date
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import auth, models, schemas
from ..database import get_db

router = APIRouter(prefix="/billing/invoices", tags=["platform billing"])


@router.get("", response_model=List[schemas.CompanyInvoiceOut])
def list_invoices(
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer", "dev_support")),
):
    query = db.query(models.CompanyInvoice)
    if company_id is not None:
        query = query.filter(models.CompanyInvoice.company_id == company_id)
    return query.order_by(models.CompanyInvoice.issued_at.desc(), models.CompanyInvoice.id.desc()).limit(2000).all()


@router.post("", response_model=schemas.CompanyInvoiceOut, status_code=201)
def create_invoice(
    payload: schemas.CompanyInvoiceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
    company = db.query(models.Company).filter(models.Company.id == payload.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if payload.due_at < payload.issued_at:
        raise HTTPException(status_code=400, detail="Due date cannot be before issue date")
    invoice = models.CompanyInvoice(
        company_id=company.id,
        invoice_number=f"HF-{date.today():%Y%m%d}-{secrets.token_hex(3).upper()}",
        description=payload.description.strip(),
        amount=payload.amount,
        currency=payload.currency.upper(),
        status="unpaid",
        issued_at=payload.issued_at,
        due_at=payload.due_at,
        notes=payload.notes,
        created_by=current_user.id,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return invoice


@router.patch("/{invoice_id}/status", response_model=schemas.CompanyInvoiceOut)
def update_invoice_status(
    invoice_id: int,
    payload: schemas.CompanyInvoiceStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("developer")),
):
    invoice = db.query(models.CompanyInvoice).filter(models.CompanyInvoice.id == invoice_id).first()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    invoice.status = payload.status
    db.commit()
    db.refresh(invoice)
    return invoice
