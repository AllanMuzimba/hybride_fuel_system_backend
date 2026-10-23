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
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
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
def list_fuel_types(
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Your company's fuel types. Pass company_id to read another tenant's
    (developer / dev_support only — the Tanks screen needs them to build a
    tank for the company they selected)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    return (
        db.query(models.FuelType)
        .filter(models.FuelType.company_id == target_company)
        .order_by(models.FuelType.name)
        .all()
    )


@router.patch("/{fuel_type_id}", response_model=schemas.FuelTypeOut)
def update_default_prices(
    fuel_type_id: int,
    payload: schemas.FuelTypeUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
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
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.require_role("manager", "director", "supervisor", "developer", "dev_support")),
):
    """Stock on hand plus tank capacity and % full, per branch per fuel
    type (v_branch_stock_status). Defaults to every branch of the caller's
    company — this is HQ's "how full is each branch's tank" view; pass
    branch_id to see just one (must be a branch of your own company), or
    company_id to read another tenant's tanks (developer / dev_support)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    if branch_id is None and current_user.branch_id is not None:
        branch_id = current_user.branch_id
    query = "SELECT * FROM v_branch_stock_status WHERE company_id = :company_id"
    params = {"company_id": target_company}
    if branch_id is not None:
        auth.verify_tank_branch(db, current_user, target_company, branch_id)
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
    current_user: models.User = Depends(auth.require_role("manager", "director", "developer")),
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
    current_user: models.User = Depends(
        auth.require_role("manager", "supervisor", "developer", "dev_support")
    ),
):
    """Set (or change) a branch's tank capacity for a fuel type — this is
    what makes percent_full meaningful and is how a tank is *created*: pick
    the company (developer / dev_support only, payload.company_id), the
    branch and the fuel type, then give it a capacity. Works even before
    that branch has received any of this fuel type yet (creates the stock
    row at 0 volume). tank_height_cm optionally sets the calibrated
    dip-stick height (cm) used to convert dip readings into litres."""
    target_company = auth.resolve_company_id(db, current_user, payload.company_id)
    auth.verify_tank_branch(db, current_user, target_company, payload.branch_id)

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != target_company:
        raise HTTPException(status_code=404, detail="Fuel type not found in that company")

    stock = db.query(models.FuelStock).get((payload.branch_id, payload.fuel_type_id))
    if not stock:
        stock = models.FuelStock(branch_id=payload.branch_id, fuel_type_id=payload.fuel_type_id, current_volume=0)
        db.add(stock)
    stock.capacity = payload.capacity
    if payload.tank_height_cm is not None:
        stock.tank_height_cm = payload.tank_height_cm
    db.commit()

    row = db.execute(
        text("SELECT * FROM v_branch_stock_status WHERE branch_id = :b AND fuel_type_id = :f"),
        {"b": payload.branch_id, "f": payload.fuel_type_id},
    ).mappings().first()
    return dict(row)


# ------------------------------------------------------------------------
# Dip-stick calibration: turn a stick reading (cm from the tank bottom)
# into litres for one branch's tank. fn_dip_to_volume() interpolates a
# stored dip chart, and falls back to a straight line — tank_height_cm
# (empty) up to capacity (full) — when no chart has been entered yet, so
# dip readings work as soon as a tank size is known.
# ------------------------------------------------------------------------
def _require_fuel_type(
    db: Session, target_company_id: int, fuel_type_id: int
) -> models.FuelType:
    fuel_type = db.query(models.FuelType).get(fuel_type_id)
    if not fuel_type or fuel_type.company_id != target_company_id:
        raise HTTPException(status_code=404, detail="Fuel type not found in that company")
    return fuel_type


def _dip_volume_payload(db: Session, branch_id: int, fuel_type_id: int, dip_cm: float) -> dict:
    volume = db.execute(
        text("SELECT fn_dip_to_volume(:b, :f, :d) AS v"),
        {"b": branch_id, "f": fuel_type_id, "d": dip_cm},
    ).scalar()
    if volume is None:
        raise HTTPException(
            status_code=422,
            detail="This tank has no calibration yet — set its capacity first "
                   "(PATCH /fuel-types/stock/capacity)",
        )
    has_chart = db.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM tank_dip_calibrations "
            "WHERE branch_id = :b AND fuel_type_id = :f)"
        ),
        {"b": branch_id, "f": fuel_type_id},
    ).scalar()
    return {
        "branch_id": branch_id,
        "fuel_type_id": fuel_type_id,
        "dip_cm": dip_cm,
        "volume_litres": float(volume),
        "source": "calibration" if has_chart else "linear",
    }


@router.get("/dip-volume", response_model=schemas.DipVolumeOut)
def dip_to_volume(
    branch_id: int,
    fuel_type_id: int,
    dip_cm: float,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """One dip-stick reading -> litres for that branch's tank (calculated by
    the database, so what the receiving screen previews is exactly what a
    receipt will store)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    auth.verify_tank_branch(db, current_user, target_company, branch_id)
    _require_fuel_type(db, target_company, fuel_type_id)
    return _dip_volume_payload(db, branch_id, fuel_type_id, dip_cm)


@router.get("/dip-calibration", response_model=schemas.DipCalibrationOut)
def get_dip_calibration(
    branch_id: int,
    fuel_type_id: int,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """The branch's dip chart for one fuel type (plus the tank height and
    capacity it falls back to when no chart points exist)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    auth.verify_tank_branch(db, current_user, target_company, branch_id)
    _require_fuel_type(db, target_company, fuel_type_id)
    return _calibration_payload(db, branch_id, fuel_type_id)


def _calibration_payload(db: Session, branch_id: int, fuel_type_id: int) -> dict:
    rows = (
        db.query(models.TankDipCalibration)
        .filter(
            models.TankDipCalibration.branch_id == branch_id,
            models.TankDipCalibration.fuel_type_id == fuel_type_id,
        )
        .order_by(models.TankDipCalibration.dip_cm)
        .all()
    )
    stock = db.query(models.FuelStock).get((branch_id, fuel_type_id))
    return {
        "branch_id": branch_id,
        "fuel_type_id": fuel_type_id,
        "tank_height_cm": float(stock.tank_height_cm) if stock and stock.tank_height_cm else None,
        "capacity": float(stock.capacity) if stock and stock.capacity else None,
        "source": "calibration" if rows else "linear",
        "points": [
            {"dip_cm": float(r.dip_cm), "volume_litres": float(r.volume_litres)} for r in rows
        ],
    }


@router.put("/dip-calibration", response_model=schemas.DipCalibrationOut)
def set_dip_calibration(
    payload: schemas.DipCalibrationUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(
        auth.require_role("manager", "supervisor", "developer", "dev_support")
    ),
):
    """Replace a branch's dip chart for one fuel type (list of
    {dip_cm, volume_litres} points, ascending). Only needed when the tank
    isn't even — until then readings are converted with tank_height_cm and
    capacity. payload.company_id lets the developer / dev_support team
    calibrate a tank belonging to another tenant."""
    target_company = auth.resolve_company_id(db, current_user, payload.company_id)
    auth.verify_tank_branch(db, current_user, target_company, payload.branch_id)
    _require_fuel_type(db, target_company, payload.fuel_type_id)

    seen = set()
    for p in payload.points:
        if p.dip_cm < 0 or p.volume_litres < 0:
            raise HTTPException(status_code=400, detail="Dip readings and volumes must not be negative")
        key = round(p.dip_cm, 2)
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate dip point at {p.dip_cm} cm")
        seen.add(key)

    db.query(models.TankDipCalibration).filter(
        models.TankDipCalibration.branch_id == payload.branch_id,
        models.TankDipCalibration.fuel_type_id == payload.fuel_type_id,
    ).delete()
    for p in payload.points:
        db.add(
            models.TankDipCalibration(
                branch_id=payload.branch_id,
                fuel_type_id=payload.fuel_type_id,
                dip_cm=p.dip_cm,
                volume_litres=p.volume_litres,
                created_by=current_user.id,
            )
        )
    db.commit()
    return _calibration_payload(db, payload.branch_id, payload.fuel_type_id)


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
    current_user: models.User = Depends(auth.require_role("manager", "developer")),
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
