from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/sales", tags=["sales"])


@router.post("", response_model=schemas.SaleOut)
def record_sale(
    payload: schemas.SaleCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),  # any logged-in user (usually attendant) can sell
):
    """Record a fuel sale at one of your company's branches. Cost/sell price
    are snapshotted from THAT BRANCH's own current price (branch_fuel_prices)
    — not a company-wide price, since branches can charge differently. Stock
    is decremented and (for 'account' sales) the customer's balance is
    updated automatically via database triggers, which also enforce the
    customer's monthly credit limit and confirm everything belongs to the
    same company as the branch."""
    auth.verify_branch_access(current_user, payload.branch_id, db)

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in your company")

    price = db.query(models.BranchFuelPrice).get((payload.branch_id, payload.fuel_type_id))
    if not price:
        raise HTTPException(
            status_code=400,
            detail="This branch has no price set for that fuel type yet — set one via PATCH /fuel-types/branch-prices",
        )

    if payload.payment_method == "account" and not payload.customer_id:
        raise HTTPException(status_code=400, detail="customer_id is required for account sales")

    sale = models.Sale(
        branch_id=payload.branch_id,
        fuel_type_id=payload.fuel_type_id,
        customer_id=payload.customer_id,
        volume=payload.volume,
        cost_price=price.current_cost,
        sell_price=price.sell_price,
        payment_method=payload.payment_method,
        served_by=current_user.id,
        notes=payload.notes,
    )
    db.add(sale)
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        # surfaces trigger errors cleanly, e.g. "Credit limit exceeded..." or "No stock..."
        raise HTTPException(status_code=400, detail=str(exc.orig))
    db.refresh(sale)
    return sale


@router.get("", response_model=List[schemas.SaleOut])
def list_sales(
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    customer_id: Optional[int] = None,
    served_by: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    q = db.query(models.Sale).join(
        models.CompanyBranch, models.CompanyBranch.id == models.Sale.branch_id
    ).filter(models.CompanyBranch.company_id == current_user.company_id)

    if start_date:
        q = q.filter(models.Sale.sale_date >= start_date)
    if end_date:
        q = q.filter(models.Sale.sale_date <= end_date)
    if branch_id:
        q = q.filter(models.Sale.branch_id == branch_id)
    if fuel_type_id:
        q = q.filter(models.Sale.fuel_type_id == fuel_type_id)
    if customer_id:
        q = q.filter(models.Sale.customer_id == customer_id)
    if served_by:
        q = q.filter(models.Sale.served_by == served_by)
    return q.order_by(models.Sale.sale_date.desc()).all()


@router.get("/{sale_id}", response_model=schemas.SaleOut)
def get_sale(sale_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    sale = db.query(models.Sale).get(sale_id)
    if not sale:
        raise HTTPException(status_code=404, detail="Sale not found")
    auth.verify_branch_access(current_user, sale.branch_id, db)
    return sale
