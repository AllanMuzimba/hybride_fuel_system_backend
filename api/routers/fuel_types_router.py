from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models, schemas, auth
from ..database import get_db

router = APIRouter(prefix="/fuel-types", tags=["fuel types & stock"])


@router.post("", response_model=schemas.FuelTypeOut)
def create_fuel_type(
    payload: schemas.FuelTypeCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Defines a fuel type for your company and its HQ reference price
    (default_cost/default_sell_price). A database trigger immediately seeds
    every existing branch's own branch_fuel_prices row from these defaults —
    branches can then move their own price independently (see the
    /fuel-types/branch-prices endpoints below)."""
    if db.query(models.FuelType).filter(
        models.FuelType.company_id == current_user.company_id, models.FuelType.name == payload.name
    ).first():
        raise HTTPException(status_code=400, detail="Fuel type already exists for your company")

    fuel_type = models.FuelType(**payload.model_dump(), company_id=current_user.company_id, updated_by=current_user.id)
    db.add(fuel_type)
    db.commit()
    db.refresh(fuel_type)
    # Note: fuel_stock rows are created lazily, per branch, the first time
    # that branch receives this fuel type (see the receipt trigger).
    return fuel_type


@router.get("", response_model=List[schemas.FuelTypeOut])
def list_fuel_types(db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    return (
        db.query(models.FuelType)
        .filter(models.FuelType.company_id == current_user.company_id)
        .order_by(models.FuelType.name)
        .all()
    )


@router.patch("/{fuel_type_id}", response_model=schemas.FuelTypeOut)
def update_default_prices(
    fuel_type_id: int,
    payload: schemas.FuelTypeUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Updates HQ's reference price for this fuel type. This does NOT change
    what any branch actually charges — each branch's live price lives in
    branch_fuel_prices and is only changed via PATCH /fuel-types/branch-prices."""
    fuel_type = db.query(models.FuelType).get(fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found")

    if payload.default_cost is not None:
        fuel_type.default_cost = payload.default_cost
    if payload.default_sell_price is not None:
        fuel_type.default_sell_price = payload.default_sell_price
    fuel_type.updated_by = current_user.id

    db.commit()
    db.refresh(fuel_type)
    return fuel_type


@router.get("/stock", response_model=List[schemas.BranchStockStatusOut])
def list_stock(
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Stock on hand plus tank capacity and % full, per branch per fuel
    type (v_branch_stock_status). Defaults to every branch of the caller's
    company — this is HQ's "how full is each branch's tank" view; pass
    branch_id to see just one (must be a branch of your own company)."""
    query = "SELECT * FROM v_branch_stock_status WHERE company_id = :company_id"
    params = {"company_id": current_user.company_id}
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
        query += " AND branch_id = :branch_id"
        params["branch_id"] = branch_id
    if fuel_type_id is not None:
        query += " AND fuel_type_id = :fuel_type_id"
        params["fuel_type_id"] = fuel_type_id
    query += " ORDER BY branch_name, fuel_type_name"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


@router.get("/stock/company-totals", response_model=List[schemas.CompanyStockTotalOut])
def company_stock_totals(
    fuel_type_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Stock and tank capacity summed across every branch, per fuel type —
    HQ's company-wide view (v_company_stock_totals)."""
    query = "SELECT * FROM v_company_stock_totals WHERE company_id = :company_id"
    params = {"company_id": current_user.company_id}
    if fuel_type_id is not None:
        query += " AND fuel_type_id = :fuel_type_id"
        params["fuel_type_id"] = fuel_type_id
    query += " ORDER BY fuel_type_name"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/stock/capacity", response_model=schemas.BranchStockStatusOut)
def set_stock_capacity(
    payload: schemas.StockCapacityUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Set (or change) a branch's tank capacity for a fuel type — this is
    what makes percent_full meaningful. Works even before that branch has
    received any of this fuel type yet (creates the stock row at 0 volume)."""
    auth.verify_branch_access(current_user, payload.branch_id, db)

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != current_user.company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in your company")

    stock = db.query(models.FuelStock).get((payload.branch_id, payload.fuel_type_id))
    if not stock:
        stock = models.FuelStock(branch_id=payload.branch_id, fuel_type_id=payload.fuel_type_id, current_volume=0)
        db.add(stock)
    stock.capacity = payload.capacity
    db.commit()

    row = db.execute(
        text("SELECT * FROM v_branch_stock_status WHERE branch_id = :b AND fuel_type_id = :f"),
        {"b": payload.branch_id, "f": payload.fuel_type_id},
    ).mappings().first()
    return dict(row)


# ------------------------------------------------------------------------
# Per-branch pricing. This is what a branch actually sells at — HQ (a
# company-wide user, branch_id NULL) can view or set any branch's price;
# a branch-tied user can only touch their own branch's.
# ------------------------------------------------------------------------
@router.get("/branch-prices", response_model=List[schemas.BranchFuelPriceOut])
def list_branch_prices(
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Every branch's current price per fuel type (v_branch_fuel_prices),
    scoped to your company. Defaults to every branch — HQ uses this to
    compare pricing across branches at a glance; pass branch_id to see one."""
    query = "SELECT * FROM v_branch_fuel_prices WHERE company_id = :company_id"
    params = {"company_id": current_user.company_id}
    if branch_id is not None:
        auth.verify_branch_access(current_user, branch_id, db)
        query += " AND branch_id = :branch_id"
        params["branch_id"] = branch_id
    if fuel_type_id is not None:
        query += " AND fuel_type_id = :fuel_type_id"
        params["fuel_type_id"] = fuel_type_id
    query += " ORDER BY branch_name, fuel_type_name"
    rows = db.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


@router.patch("/branch-prices", response_model=schemas.BranchFuelPriceOut)
def update_branch_price(
    payload: schemas.BranchFuelPriceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
):
    """Set one branch's selling price and/or cost for one fuel type. A
    manager tied to that branch can only move their own branch's price;
    a company-wide manager/director/developer can move any branch's.
    Every change is logged to the activity log for HQ audit (via trigger)."""
    branch = auth.verify_branch_access(current_user, payload.branch_id, db)

    price = db.query(models.BranchFuelPrice).get((payload.branch_id, payload.fuel_type_id))
    if not price:
        raise HTTPException(
            status_code=404,
            detail="No price row for this branch/fuel type — has this fuel type been created for your company?",
        )

    if payload.current_cost is not None:
        price.current_cost = payload.current_cost
    if payload.sell_price is not None:
        price.sell_price = payload.sell_price
    price.updated_by = current_user.id

    db.commit()

    row = db.execute(
        text("SELECT * FROM v_branch_fuel_prices WHERE branch_id = :b AND fuel_type_id = :f"),
        {"b": payload.branch_id, "f": payload.fuel_type_id},
    ).mappings().first()
    return dict(row)
