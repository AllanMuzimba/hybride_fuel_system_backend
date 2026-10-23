from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/suppliers", tags=["suppliers"])

# Any company user from supervisor up may keep the supplier book in shape;
# attendants only ever read (through the receiving screen's search box).
SUPPLIER_WRITERS = ("supervisor", "manager", "developer", "dev_support")


def _supplier_for(db: Session, current_user: models.User, supplier_id: int) -> models.Supplier:
    supplier = db.query(models.Supplier).get(supplier_id)
    if not supplier or supplier.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


def _check_name(db: Session, current_user: models.User, name: str, except_id: Optional[int] = None):
    qry = db.query(models.Supplier).filter(
        models.Supplier.company_id == current_user.company_id,
        models.Supplier.name == name,
    )
    if except_id is not None:
        qry = qry.filter(models.Supplier.id != except_id)
    if qry.first():
        raise HTTPException(status_code=400, detail="A supplier with that name already exists")


@router.post("", response_model=schemas.SupplierOut)
def create_supplier(
    payload: schemas.SupplierCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*SUPPLIER_WRITERS)),
):
    """Register a new supplier for the caller's company (company name,
    contact person, phone, email, address, optional VAT/tax number).

    Every company user from supervisor up may do this, branch-tied or not —
    a supervisor who has just met a new tanker can add them on the spot."""
    _check_name(db, current_user, payload.name)

    supplier = models.Supplier(**payload.model_dump(), company_id=current_user.company_id, created_by=current_user.id)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.get("", response_model=List[schemas.SupplierOut])
def list_suppliers(
    active_only: bool = True,
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Suppliers of the caller's own company only. Pass q= to search by
    company name, contact person or phone (used by the receiving screen's
    supplier search box) and active_only=false to include deactivated
    accounts (the Suppliers page's "restore" action)."""
    qry = db.query(models.Supplier).filter(models.Supplier.company_id == current_user.company_id)
    if active_only:
        qry = qry.filter(models.Supplier.is_active.is_(True))
    if q and q.strip():
        term = f"%{q.strip()}%"
        qry = qry.filter(
            models.Supplier.name.ilike(term)
            | models.Supplier.contact_person.ilike(term)
            | models.Supplier.phone.ilike(term)
        )
    return qry.order_by(models.Supplier.name).all()


@router.get("/{supplier_id}", response_model=schemas.SupplierOut)
def get_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    return _supplier_for(db, current_user, supplier_id)


@router.put("/{supplier_id}", response_model=schemas.SupplierOut)
def update_supplier(
    supplier_id: int,
    payload: schemas.SupplierUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*SUPPLIER_WRITERS)),
):
    """Edit a supplier account (all fields optional — only the ones you send
    are changed)."""
    supplier = _supplier_for(db, current_user, supplier_id)
    data = payload.model_dump(exclude_unset=True)

    if "name" in data and data["name"]:
        _check_name(db, current_user, data["name"], except_id=supplier_id)
        supplier.name = data["name"]
    for field in ("contact_person", "phone", "email", "address", "vat_number", "is_active"):
        if field in data:
            setattr(supplier, field, data[field])

    db.commit()
    db.refresh(supplier)
    return supplier


@router.patch("/{supplier_id}/deactivate", response_model=schemas.SupplierOut)
def deactivate_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*SUPPLIER_WRITERS)),
):
    """Retire a supplier (soft delete — past invoices and receipts keep
    pointing at it, which is why suppliers are never hard-deleted)."""
    supplier = _supplier_for(db, current_user, supplier_id)
    supplier.is_active = False
    db.commit()
    db.refresh(supplier)
    return supplier


@router.patch("/{supplier_id}/activate", response_model=schemas.SupplierOut)
def activate_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*SUPPLIER_WRITERS)),
):
    supplier = _supplier_for(db, current_user, supplier_id)
    supplier.is_active = True
    db.commit()
    db.refresh(supplier)
    return supplier


@router.delete("/{supplier_id}", response_model=schemas.SupplierOut)
def delete_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role(*SUPPLIER_WRITERS)),
):
    """CRUD's D: deactivates the supplier so it disappears from the search
    box and the book, while its invoice history stays intact."""
    supplier = _supplier_for(db, current_user, supplier_id)
    supplier.is_active = False
    db.commit()
    db.refresh(supplier)
    return supplier
