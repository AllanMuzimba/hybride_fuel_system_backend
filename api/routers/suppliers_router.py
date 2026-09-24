from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.post("", response_model=schemas.SupplierOut)
def create_supplier(
    payload: schemas.SupplierCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("supervisor", "manager", "director", "developer")),
):
    supplier = models.Supplier(**payload.model_dump(), company_id=current_user.company_id, created_by=current_user.id)
    db.add(supplier)
    db.commit()
    db.refresh(supplier)
    return supplier


@router.get("", response_model=List[schemas.SupplierOut])
def list_suppliers(
    active_only: bool = True,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Suppliers of the caller's own company only."""
    q = db.query(models.Supplier).filter(models.Supplier.company_id == current_user.company_id)
    if active_only:
        q = q.filter(models.Supplier.is_active.is_(True))
    return q.order_by(models.Supplier.name).all()


@router.get("/{supplier_id}", response_model=schemas.SupplierOut)
def get_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    supplier = db.query(models.Supplier).get(supplier_id)
    if not supplier or supplier.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return supplier


@router.patch("/{supplier_id}/deactivate", response_model=schemas.SupplierOut)
def deactivate_supplier(
    supplier_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    supplier = db.query(models.Supplier).get(supplier_id)
    if not supplier or supplier.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Supplier not found")
    supplier.is_active = False
    db.commit()
    db.refresh(supplier)
    return supplier
