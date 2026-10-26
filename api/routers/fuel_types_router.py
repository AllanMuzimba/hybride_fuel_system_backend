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
    current_user: models.User = Depends(
        auth.require_role("manager", "director", "developer", "dev_support")
    ),
):
    """Defines a fuel type for your company and its HQ reference price
    (default_cost/default_sell_price). A database trigger immediately seeds
    every existing branch's own branch_fuel_prices row from these defaults —
    branches can then move their own price independently (see the
    /fuel-types/branch-prices endpoints below). developer / dev_support may
    name payload.company_id to define it for another tenant."""
    target_company = auth.resolve_company_id(db, current_user, payload.company_id)
    name = payload.name.strip()
    if db.query(models.FuelType).filter(
        models.FuelType.company_id == target_company, models.FuelType.name == name
    ).first():
        raise HTTPException(status_code=400, detail="Fuel type already exists for your company")

    values = payload.model_dump()
    values.pop("company_id", None)
    values["name"] = name
    fuel_type = models.FuelType(**values, company_id=target_company, updated_by=current_user.id)
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
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(
        auth.require_role("manager", "director", "developer", "dev_support")
    ),
):
    """Renames the fuel type and/or updates HQ's reference price for it.
    Branch selling prices are unchanged — each branch's live price lives in
    branch_fuel_prices and only moves through the branch-prices endpoints.
    developer / dev_support may pass company_id for another tenant."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    fuel_type = db.query(models.FuelType).get(fuel_type_id)
    if not fuel_type or fuel_type.company_id != target_company:
        raise HTTPException(status_code=404, detail="Fuel type not found")

    if payload.name is not None:
        name = payload.name.strip()
        if db.query(models.FuelType).filter(
            models.FuelType.company_id == target_company,
            models.FuelType.name == name,
            models.FuelType.id != fuel_type.id,
        ).first():
            raise HTTPException(status_code=400, detail="Fuel type already exists for your company")
        fuel_type.name = name
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
    if payload.tank_id is not None:
        tank = db.query(models.FuelTank).get(payload.tank_id)
        if not tank or tank.branch_id != payload.branch_id or tank.fuel_type_id != payload.fuel_type_id:
            raise HTTPException(status_code=404, detail="Tank not found for that branch and fuel type")
    else:
        tank_name = (payload.tank_name or "").strip()
        if not tank_name:
            count = db.query(models.FuelTank).filter(
                models.FuelTank.branch_id == payload.branch_id,
                models.FuelTank.fuel_type_id == payload.fuel_type_id,
            ).count()
            tank_name = f"{fuel_type.name} Tank {count + 1}"
        tank = models.FuelTank(
            branch_id=payload.branch_id, fuel_type_id=payload.fuel_type_id,
            name=tank_name, current_volume=0, capacity=payload.capacity,
        )
        db.add(tank)
    if payload.tank_name is not None:
        tank.name = payload.tank_name.strip()
    tank.capacity = payload.capacity
    if payload.tank_height_cm is not None:
        tank.tank_height_cm = payload.tank_height_cm

    # Legacy aggregate capacity is retained for compatibility, but is always
    # the sum of physical tanks so it can never be double-counted in reports.
    db.flush()
    totals = db.query(models.FuelTank).filter(
        models.FuelTank.branch_id == payload.branch_id,
        models.FuelTank.fuel_type_id == payload.fuel_type_id,
    ).all()
    stock.capacity = sum(float(t.capacity) for t in totals)
    db.commit()

    row = db.execute(
        text("SELECT * FROM v_branch_stock_status WHERE tank_id = :t"),
        {"t": tank.id},
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


def _dip_volume_payload(db: Session, tank: models.FuelTank, dip_cm: float) -> dict:
    volume = db.execute(
        text("SELECT fn_tank_dip_to_volume(:t, :d) AS v"),
        {"t": tank.id, "d": dip_cm},
    ).scalar()
    if volume is None:
        raise HTTPException(
            status_code=422,
            detail="Tank calibration could not be evaluated",
        )
    has_chart = db.execute(
        text(
            "SELECT EXISTS(SELECT 1 FROM tank_dip_calibrations "
            "WHERE tank_id = :t)"
        ),
        {"t": tank.id},
    ).scalar()
    return {
        "tank_id": tank.id,
        "branch_id": tank.branch_id,
        "fuel_type_id": tank.fuel_type_id,
        "dip_cm": dip_cm,
        "volume_litres": float(volume),
        "source": "calibration" if has_chart else "linear",
    }


@router.get("/dip-volume", response_model=schemas.DipVolumeOut)
def dip_to_volume(
    tank_id: int,
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
    tank = db.query(models.FuelTank).get(tank_id)
    if not tank or tank.branch_id != branch_id or tank.fuel_type_id != fuel_type_id:
        raise HTTPException(status_code=404, detail="Tank not found for that branch and fuel type")
    return _dip_volume_payload(db, tank, dip_cm)


@router.get("/dip-calibration", response_model=schemas.DipCalibrationOut)
def get_dip_calibration(
    tank_id: int,
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
    tank = db.query(models.FuelTank).get(tank_id)
    if not tank or tank.branch_id != branch_id or tank.fuel_type_id != fuel_type_id:
        raise HTTPException(status_code=404, detail="Tank not found for that branch and fuel type")
    return _calibration_payload(db, tank)


def _calibration_payload(db: Session, tank: models.FuelTank) -> dict:
    rows = (
        db.query(models.TankDipCalibration)
        .filter(
            models.TankDipCalibration.tank_id == tank.id,
        )
        .order_by(models.TankDipCalibration.dip_cm)
        .all()
    )
    return {
        "tank_id": tank.id,
        "branch_id": tank.branch_id,
        "fuel_type_id": tank.fuel_type_id,
        "tank_height_cm": float(tank.tank_height_cm),
        "capacity": float(tank.capacity),
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
    tank = db.query(models.FuelTank).get(payload.tank_id)
    if not tank or tank.branch_id != payload.branch_id or tank.fuel_type_id != payload.fuel_type_id:
        raise HTTPException(status_code=404, detail="Tank not found for that branch and fuel type")

    seen = set()
    for p in payload.points:
        if p.dip_cm < 0 or p.volume_litres < 0:
            raise HTTPException(status_code=400, detail="Dip readings and volumes must not be negative")
        key = round(p.dip_cm, 2)
        if key in seen:
            raise HTTPException(status_code=400, detail=f"Duplicate dip point at {p.dip_cm} cm")
        seen.add(key)

    db.query(models.TankDipCalibration).filter(
        models.TankDipCalibration.tank_id == payload.tank_id,
    ).delete()
    for p in payload.points:
        db.add(
            models.TankDipCalibration(
                tank_id=payload.tank_id,
                branch_id=payload.branch_id,
                fuel_type_id=payload.fuel_type_id,
                dip_cm=p.dip_cm,
                volume_litres=p.volume_litres,
                created_by=current_user.id,
            )
        )
    db.commit()
    return _calibration_payload(db, tank)


# ------------------------------------------------------------------------
# Per-branch pricing. This is what a branch actually sells at — HQ (a
# company-wide user, branch_id NULL) can view or set any branch's price;
# a branch-tied user can only touch their own branch's.
# ------------------------------------------------------------------------
@router.get("/branch-prices", response_model=List[schemas.BranchFuelPriceOut])
def list_branch_prices(
    branch_id: Optional[int] = None,
    fuel_type_id: Optional[int] = None,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    """Every branch's current price per fuel type (v_branch_fuel_prices),
    scoped to your company. Defaults to every branch — HQ uses this to
    compare pricing across branches at a glance; pass branch_id to see one,
    or company_id to read another tenant (developer / dev_support only)."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    query = "SELECT * FROM v_branch_fuel_prices WHERE company_id = :company_id"
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


@router.patch("/branch-prices", response_model=schemas.BranchFuelPriceOut)
def update_branch_price(
    payload: schemas.BranchFuelPriceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(
        auth.require_role("manager", "director", "developer", "dev_support")
    ),
):
    """Set one branch's selling price and/or cost for one fuel type. A
    manager tied to that branch can only move their own branch's price;
    a company-wide manager/director can move any branch's; developer and
    dev_support may price any branch of any company. Every change is logged
    to the activity log for HQ audit (via trigger)."""
    branch = db.query(models.CompanyBranch).get(payload.branch_id)
    if branch is None:
        raise HTTPException(status_code=404, detail="Branch not found")
    target_company = auth.resolve_company_id(db, current_user, branch.company_id)
    auth.verify_tank_branch(db, current_user, target_company, payload.branch_id)

    fuel_type = db.query(models.FuelType).get(payload.fuel_type_id)
    if not fuel_type or fuel_type.company_id != target_company:
        raise HTTPException(status_code=404, detail="Fuel type not found in that company")

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


@router.put("/{fuel_type_id}/branch-prices")
def apply_branch_prices(
    fuel_type_id: int,
    payload: schemas.BranchPriceApply,
    company_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(
        auth.require_role("manager", "director", "developer", "dev_support")
    ),
):
    """Reprice one fuel type across several branches of a company in a
    single call (the fuel type must belong to that company — 404
    otherwise). developer / dev_support may pass company_id for another
    tenant.

    Omit branch_ids to hit every branch of the company; list branch_ids to
    hit only those (each must be one of its branches — 404 — and a user
    tied to one branch may only name their own — 403). Whichever of
    current_cost / sell_price was sent is written to each branch's
    branch_fuel_prices row — created from the payload when a branch has no
    row yet — together with updated_by. Answers with how many branches were
    touched and their ids."""
    target_company = auth.resolve_company_id(db, current_user, company_id)
    fuel_type = db.query(models.FuelType).get(fuel_type_id)
    if not fuel_type or fuel_type.company_id != target_company:
        raise HTTPException(status_code=404, detail="Fuel type not found")

    if payload.branch_ids is None:
        target_ids = [
            branch.id
            for branch in db.query(models.CompanyBranch)
            .filter(models.CompanyBranch.company_id == target_company)
            .order_by(models.CompanyBranch.id)
            .all()
        ]
    else:
        target_ids = []
        for branch_id in payload.branch_ids:
            auth.verify_tank_branch(db, current_user, target_company, branch_id)
            if branch_id not in target_ids:
                target_ids.append(branch_id)

    for branch_id in target_ids:
        price = db.query(models.BranchFuelPrice).get((branch_id, fuel_type_id))
        if not price:
            price = models.BranchFuelPrice(
                branch_id=branch_id,
                fuel_type_id=fuel_type_id,
                current_cost=payload.current_cost if payload.current_cost is not None else 0,
                sell_price=payload.sell_price if payload.sell_price is not None else 0,
            )
            db.add(price)
        if payload.current_cost is not None:
            price.current_cost = payload.current_cost
        if payload.sell_price is not None:
            price.sell_price = payload.sell_price
        price.updated_by = current_user.id
    db.commit()

    return {"updated": len(target_ids), "branch_ids": target_ids}
